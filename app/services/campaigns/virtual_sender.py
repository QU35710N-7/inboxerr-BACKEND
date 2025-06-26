# app/services/campaigns/virtual_sender.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, AsyncGenerator
import uuid
import re

from app.core.config import settings
from app.db.repositories.campaigns import CampaignRepository
from app.db.repositories.contacts import ContactRepository
from app.db.repositories.templates import TemplateRepository
from app.db.repositories.messages import MessageRepository
from app.schemas.message import MessageStatus
from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType
from app.services.sms.sender import SMSSender, get_sms_sender
from app.db.session import get_repository_context

logger = logging.getLogger("inboxerr.virtual_sender")

class VirtualMessage:
    """Virtual message object for on-demand generation."""
    
    def __init__(
        self,
        phone_number: str,
        message_text: str,
        contact_name: Optional[str] = None,
        contact_data: Optional[Dict[str, Any]] = None,
        campaign_id: Optional[str] = None,
        user_id: Optional[str] = None
    ):
        self.phone_number = phone_number
        self.message_text = message_text
        self.contact_name = contact_name
        self.contact_data = contact_data or {}
        self.campaign_id = campaign_id
        self.user_id = user_id
        self.custom_id = str(uuid.uuid4())

class VirtualCampaignSender:
    """
    Service for sending campaigns using virtual messaging approach.
    
    Generates messages on-demand from templates and contacts without
    pre-creating message records in the database.
    """
    
    def __init__(self, sms_sender: SMSSender, event_bus: Any):
        """Initialize with SMS sender and event bus."""
        self.sms_sender = sms_sender
        self.event_bus = event_bus
        self._chunk_size = settings.BATCH_SIZE or 100
        self._semaphore = asyncio.Semaphore(5)
    
    async def process_virtual_campaign(self, campaign_id: str) -> bool:
        """
        Process a virtual campaign by generating messages on-demand.
        
        Args:
            campaign_id: Campaign ID
            
        Returns:
            bool: True if processing started successfully
        """
        try:
            # Get campaign details
            async with get_repository_context(CampaignRepository) as campaign_repo:
                campaign = await campaign_repo.get_by_id(campaign_id)
                if not campaign or campaign.status != "active":
                    return False
                
                # Check if this is a virtual campaign
                is_virtual = campaign.settings.get("virtual_messaging", False)
                if not is_virtual:
                    logger.warning(f"Campaign {campaign_id} is not a virtual campaign")
                    return False
                
                import_job_id = campaign.settings.get("import_job_id")
                if not import_job_id:
                    logger.error(f"Campaign {campaign_id} missing import_job_id")
                    return False
            
            # Process in chunks with semaphore
            async with self._semaphore:
                await self._process_virtual_campaign_chunks(
                    campaign_id, import_job_id, campaign.template_id, campaign.user_id
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing virtual campaign {campaign_id}: {e}", exc_info=True)
            # Update campaign status to failed
            try:
                async with get_repository_context(CampaignRepository) as campaign_repo:
                    await campaign_repo.update_campaign_status(
                        campaign_id=campaign_id,
                        status="failed",
                        completed_at=datetime.now(timezone.utc)
                    )
            except Exception as update_error:
                logger.error(f"Failed to update campaign status: {update_error}")
            return False
    
    async def _process_virtual_campaign_chunks(
        self, 
        campaign_id: str, 
        import_job_id: str, 
        template_id: str,
        user_id: str
    ) -> None:
        """Process virtual campaign in chunks."""
        
        # Get template content
        template_content = None
        async with get_repository_context(TemplateRepository) as template_repo:
            template = await template_repo.get_by_id(template_id)
            if not template:
                raise ValueError(f"Template {template_id} not found")
            template_content = template.content
        
        # Process contacts in chunks
        offset = 0
        total_sent = 0
        total_failed = 0
        
        while True:
            # Check if campaign is still active
            async with get_repository_context(CampaignRepository) as campaign_repo:
                campaign = await campaign_repo.get_by_id(campaign_id)
                if not campaign or campaign.status != "active":
                    logger.info(f"Campaign {campaign_id} no longer active, stopping")
                    return
            
            # Get next chunk of contacts
            contacts = []
            async with get_repository_context(ContactRepository) as contact_repo:
                contacts, _ = await contact_repo.get_by_import_id(
                    import_job_id, skip=offset, limit=self._chunk_size
                )
            
            if not contacts:
                logger.info(f"No more contacts for campaign {campaign_id}")
                # ── Count the real number of messages once ───────────────────────────
                async with get_repository_context(MessageRepository) as msg_repo:
                    real_total = await msg_repo.count_messages_for_campaign(campaign_id)

                # Mark campaign as completed
                async with get_repository_context(CampaignRepository) as campaign_repo:
                    # Re-read (FOR UPDATE) in case another worker finished first
                    campaign = await campaign_repo.get_by_id(campaign_id)
                    if campaign and campaign.status != "completed":
                        await campaign_repo.update(
                            id=campaign_id,
                            obj_in={
                                "status": "completed",
                                "completed_at": datetime.now(timezone.utc),
                                "total_messages": real_total
                            }
                        )
                        logger.info(
                            "Campaign %s completed · sent=%d · total_messages=%d",
                            campaign_id, campaign.sent_count, real_total
                        )

                return
            
            # Generate and send virtual messages for this chunk
            chunk_sent, chunk_failed = await self._process_contact_chunk(
                contacts, template_content, campaign_id, user_id
            )
            
            total_sent += chunk_sent
            total_failed += chunk_failed
            
            # Update campaign statistics
            async with get_repository_context(CampaignRepository) as campaign_repo:
                await campaign_repo.update_campaign_stats(
                    campaign_id=campaign_id,
                    increment_sent=chunk_sent,
                    increment_failed=chunk_failed
                )
            
            # Move to next chunk
            offset += len(contacts)
            
            # Small delay between chunks
            await asyncio.sleep(0.5)
    
    async def _process_contact_chunk(
        self, 
        contacts: List[Any], 
        template_content: str,
        campaign_id: str,
        user_id: str
    ) -> tuple[int, int]:
        """Process a chunk of contacts and send virtual messages."""
        
        sent_count = 0
        failed_count = 0
        
        for contact in contacts:
            try:
                
                # DEDUPLICATION CHECK Check if already sent to this contact
                already_sent = await self._check_already_sent(campaign_id, contact.phone)
                if already_sent:
                    logger.debug(f"Skipping {contact.phone} - already processed for campaign {campaign_id}")
                    continue

                # Generate virtual message
                virtual_message = self._generate_virtual_message(
                    contact, template_content, campaign_id, user_id
                )
                
                # Send message using SMS sender
                result = await self.sms_sender._send_to_gateway(
                    phone_number=virtual_message.phone_number,
                    message_text=virtual_message.message_text,
                    custom_id=virtual_message.custom_id
                )
                
                # Create message record after successful send
                await self._create_message_record(
                    virtual_message, result, MessageStatus.SENT
                )
                
                sent_count += 1
                
                # Delay between messages
                await asyncio.sleep(settings.DELAY_BETWEEN_SMS)
                
            except Exception as e:
                logger.error(f"Error sending virtual message to {contact.phone}: {e}")
                
                # Create failed message record
                try:
                    virtual_message = self._generate_virtual_message(
                        contact, template_content, campaign_id, user_id
                    )
                    await self._create_message_record(
                        virtual_message, {"error": str(e)}, MessageStatus.FAILED
                    )
                except Exception as record_error:
                    logger.error(f"Failed to create failed message record: {record_error}")
                
                failed_count += 1
        
        logger.info(f"Processed chunk: {sent_count} sent, {failed_count} failed")
        return sent_count, failed_count
    
    def _generate_virtual_message(
        self, 
        contact: Any, 
        template_content: str,
        campaign_id: str,
        user_id: str
    ) -> VirtualMessage:
        """Generate a virtual message from contact and template."""
        
        # Perform template substitution
        personalized_message = template_content
        
        # Replace variables
        if contact.name:
            personalized_message = personalized_message.replace("{{name}}", contact.name)
            personalized_message = personalized_message.replace("{{contact_name}}", contact.name)
        
        personalized_message = personalized_message.replace("{{phone}}", contact.phone)
        
        # Additional contact data substitution if available
        if hasattr(contact, 'custom_fields') and contact.custom_fields:
            for key, value in contact.custom_fields.items():
                personalized_message = personalized_message.replace(f"{{{{{key}}}}}", str(value))
        
        return VirtualMessage(
            phone_number=contact.phone,
            message_text=personalized_message,
            contact_name=contact.name,
            contact_data={
                "name": contact.name,
                "tags": contact.tags or []
            },
            campaign_id=campaign_id,
            user_id=user_id
        )
    

    async def _check_already_sent(self, campaign_id: str, phone_number: str) -> bool:
        """
        Check if we've already sent a message to this phone number for this campaign.
        
        Industry standard: Idempotency check to prevent duplicate sends.
        
        Args:
            campaign_id: Campaign ID
            phone_number: Phone number to check
            
        Returns:
            bool: True if already sent, False if safe to send
        """
        try:
            async with get_repository_context(MessageRepository) as message_repo:
                # Check if any message exists for this campaign + phone combination
                # This covers SENT, FAILED, PENDING - any status means "already processed"
                existing_message = await message_repo.get_message_by_campaign_and_phone(
                    campaign_id=campaign_id,
                    phone_number=phone_number
                )
                
                if existing_message:
                    logger.debug(f"Already sent to {phone_number} in campaign {campaign_id}")
                    return True
                    
                return False
                
        except Exception as e:
            logger.error(f"Error checking duplicate send: {e}")
            # On error, assume already sent to be safe (fail-safe approach)
            return True
    
    async def _create_message_record(
        self, 
        virtual_message: VirtualMessage, 
        send_result: Dict[str, Any],
        status: MessageStatus
    ) -> None:
        """Create message record after sending (for tracking/analytics)."""
        
        try:
            async with get_repository_context(MessageRepository) as message_repo:
                #insert – repo auto-sets PENDING/SCHEDULED
                message = await message_repo.create_message(
                    phone_number=virtual_message.phone_number,
                    message_text=virtual_message.message_text,
                    user_id=virtual_message.user_id,
                    campaign_id=virtual_message.campaign_id,
                    custom_id=virtual_message.custom_id,
                    metadata={
                        "contact_name": virtual_message.contact_name,
                        "contact_data": virtual_message.contact_data,
                        "virtual_generated": True,
                        "send_result": send_result
                    }
                )

                # 2) bump status so dashboards are current
                await message_repo.update_message_status(
                    message_id=message.id,
                    status=status,
                    event_type="virtual_send",
                    gateway_message_id=send_result.get("gateway_message_id"),
                    data=send_result,
                )

        except Exception as e:
            logger.error(f"Failed to create message record: {e}")
            # Don't fail the whole operation if record creation fails

# Dependency injection
async def get_virtual_campaign_sender() -> VirtualCampaignSender:
    """Get virtual campaign sender service instance."""
    sms_sender = await get_sms_sender()
    event_bus = get_event_bus()
    return VirtualCampaignSender(sms_sender, event_bus)