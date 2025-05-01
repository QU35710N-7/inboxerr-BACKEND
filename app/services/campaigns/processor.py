# app/services/campaigns/processor.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import uuid

from app.core.config import settings
from app.db.repositories.campaigns import CampaignRepository
from app.db.repositories.messages import MessageRepository
from app.schemas.message import MessageStatus
from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType
from app.services.sms.sender import SMSSender, get_sms_sender
from app.db.session import get_repository_context

logger = logging.getLogger("inboxerr.campaigns")

class CampaignProcessor:
    """
    Service for processing SMS campaigns.
    
    Manages campaign execution, chunked processing, and status tracking.
    Uses context managers for database operations to prevent connection leaks.
    """
    
    def __init__(
        self,
        sms_sender: SMSSender,
        event_bus: Any
    ):
        """
        Initialize campaign processor with required dependencies.
        
        Removed repository dependencies to prevent long-lived connections.
        The repositories will be created as needed using context managers.
        
        Args:
            sms_sender: SMS sender service
            event_bus: Event bus for publishing events
        """
        self.sms_sender = sms_sender
        self.event_bus = event_bus
        self._processing_campaigns = set()
        self._chunk_size = settings.BATCH_SIZE  # Default from settings
        self._semaphore = asyncio.Semaphore(5)  # Limit concurrent campaigns
    
    async def start_campaign(self, campaign_id: str, user_id: str) -> bool:
        """
        Start a campaign.
        
        Args:
            campaign_id: Campaign ID
            user_id: User ID for authorization
            
        Returns:
            bool: True if campaign was started, False otherwise
        """
        # Use context manager for repository access
        async with get_repository_context(CampaignRepository) as campaign_repository:
            # Get campaign
            campaign = await campaign_repository.get_by_id(campaign_id)
            if not campaign:
                return False
            
            # Validate ownership
            if campaign.user_id != user_id:
                return False
            
            # Check if campaign can be started
            if campaign.status != "draft" and campaign.status != "paused":
                return False
            
            # Update status to active
            updated = await campaign_repository.update_campaign_status(
                campaign_id=campaign_id,
                status="active",
                started_at=datetime.now(timezone.utc)
            )
            
            if not updated:
                return False
            
            # Get total_messages for the event
            total_messages = campaign.total_messages
        
        # Start processing in background
        asyncio.create_task(self._process_campaign(campaign_id))
        
        # Publish event
        await self.event_bus.publish(
            EventType.BATCH_CREATED,
            {
                "campaign_id": campaign_id,
                "user_id": user_id,
                "total_messages": total_messages
            }
        )
        
        return True
    
    async def pause_campaign(self, campaign_id: str, user_id: str) -> bool:
        """
        Pause a campaign.
        
        Args:
            campaign_id: Campaign ID
            user_id: User ID for authorization
            
        Returns:
            bool: True if campaign was paused, False otherwise
        """
        # Use context manager for repository access
        async with get_repository_context(CampaignRepository) as campaign_repository:
            # Get campaign
            campaign = await campaign_repository.get_by_id(campaign_id)
            if not campaign:
                return False
            
            # Validate ownership
            if campaign.user_id != user_id:
                return False
            
            # Check if campaign can be paused
            if campaign.status != "active":
                return False
            
            # Update status to paused
            updated = await campaign_repository.update_campaign_status(
                campaign_id=campaign_id,
                status="paused"
            )
            
            return updated is not None
    
    async def cancel_campaign(self, campaign_id: str, user_id: str) -> bool:
        """
        Cancel a campaign.
        
        Args:
            campaign_id: Campaign ID
            user_id: User ID for authorization
            
        Returns:
            bool: True if campaign was cancelled, False otherwise
        """
        # Use context manager for repository access
        async with get_repository_context(CampaignRepository) as campaign_repository:
            # Get campaign
            campaign = await campaign_repository.get_by_id(campaign_id)
            if not campaign:
                return False
            
            # Validate ownership
            if campaign.user_id != user_id:
                return False
            
            # Check if campaign can be cancelled
            if campaign.status in ["completed", "cancelled", "failed"]:
                return False
            
            # Update status to cancelled
            updated = await campaign_repository.update_campaign_status(
                campaign_id=campaign_id,
                status="cancelled",
                completed_at=datetime.now(timezone.utc)
            )
            
            return updated is not None
    
    async def _process_campaign(self, campaign_id: str) -> None:
        """
        Process a campaign in the background.
        
        Args:
            campaign_id: Campaign ID
        """
        if campaign_id in self._processing_campaigns:
            logger.warning(f"Campaign {campaign_id} is already being processed")
            return
        
        # Mark as processing
        self._processing_campaigns.add(campaign_id)
        
        try:
            # Check campaign status with context manager
            async with get_repository_context(CampaignRepository) as campaign_repository:
                campaign = await campaign_repository.get_by_id(campaign_id)
                if not campaign or campaign.status != "active":
                    return
            
            # Process in chunks until complete
            async with self._semaphore:
                await self._process_campaign_chunks(campaign_id)
                
        except Exception as e:
            logger.error(f"Error processing campaign {campaign_id}: {e}", exc_info=True)
            # Update campaign status to failed with a new context manager
            try:
                async with get_repository_context(CampaignRepository) as campaign_repository:
                    await campaign_repository.update_campaign_status(
                        campaign_id=campaign_id,
                        status="failed",
                        completed_at=datetime.now(timezone.utc)
                    )
            except Exception as update_error:
                logger.error(f"Failed to update campaign status: {update_error}")
        finally:
            # Remove from processing set
            self._processing_campaigns.remove(campaign_id)
    
    async def _process_campaign_chunks(self, campaign_id: str) -> None:
        """
        Process campaign messages in chunks.
        
        Args:
            campaign_id: Campaign ID
        """
        # Query pending messages in chunks
        offset = 0
        
        while True:
            # Check if campaign is still active with context manager
            campaign = None
            async with get_repository_context(CampaignRepository) as campaign_repository:
                campaign = await campaign_repository.get_by_id(campaign_id)
                if not campaign or campaign.status != "active":
                    logger.info(f"Campaign {campaign_id} is no longer active, stopping processing")
                    return
            
            # Get next chunk of messages with context manager
            messages = []
            total = 0
            async with get_repository_context(MessageRepository) as message_repository:
                messages, total = await message_repository.get_messages_for_campaign(
                    campaign_id=campaign_id,
                    status=MessageStatus.PENDING,
                    skip=offset,
                    limit=self._chunk_size
                )
            
            # If no more messages, campaign is complete
            if not messages:
                logger.info(f"No more pending messages for campaign {campaign_id}")
                async with get_repository_context(CampaignRepository) as campaign_repository:
                    await campaign_repository.update_campaign_status(
                        campaign_id=campaign_id,
                        status="completed",
                        completed_at=datetime.now(timezone.utc)
                    )
                return
            
            # Process this chunk
            await self._process_message_chunk(campaign_id, messages)
            
            # Update offset for next chunk
            offset += len(messages)
            
            # Small delay between chunks to avoid overloading
            await asyncio.sleep(0.5)
    
    async def _process_message_chunk(self, campaign_id: str, messages: List[Any]) -> None:
        """
        Process a chunk of messages with proper context management.
        
        Args:
            campaign_id: Campaign ID
            messages: List of message objects
        """
        # Process each message in the chunk
        success_count = 0
        fail_count = 0
        
        for message in messages:
            try:
                # Use SMS sender to send the message
                result = await self.sms_sender._send_to_gateway(
                    phone_number=message.phone_number,
                    message_text=message.message,
                    custom_id=message.custom_id or str(uuid.uuid4())
                )
                
                # Update message status with context manager
                async with get_repository_context(MessageRepository) as message_repository:
                    await message_repository.update_message_status(
                        message_id=message.id,
                        status=result.get("status", MessageStatus.PENDING),
                        event_type="campaign_process",
                        gateway_message_id=result.get("gateway_message_id"),
                        data=result
                    )
                
                success_count += 1
                
                # Add delay between messages to avoid overloading gateway
                await asyncio.sleep(settings.DELAY_BETWEEN_SMS)
                
            except Exception as e:
                logger.error(f"Error processing message {message.id}: {e}")
                
                # Update message status to failed with context manager
                try:
                    async with get_repository_context(MessageRepository) as message_repository:
                        await message_repository.update_message_status(
                            message_id=message.id,
                            status=MessageStatus.FAILED,
                            event_type="campaign_process_error",
                            reason=str(e),
                            data={"error": str(e)}
                        )
                except Exception as update_error:
                    logger.error(f"Failed to update message status: {update_error}")
                
                fail_count += 1
        
        # Update campaign stats with context manager
        if success_count > 0 or fail_count > 0:
            try:
                async with get_repository_context(CampaignRepository) as campaign_repository:
                    await campaign_repository.update_campaign_stats(
                        campaign_id=campaign_id,
                        increment_sent=success_count,
                        increment_failed=fail_count
                    )
            except Exception as update_error:
                logger.error(f"Failed to update campaign stats: {update_error}")
        
        logger.info(f"Processed chunk for campaign {campaign_id}: {success_count} sent, {fail_count} failed")


# Dependency injection function
async def get_campaign_processor():
    """
    Get campaign processor service instance.
    
    Uses the SMS sender with its dedicated context management but doesn't create
    long-lived repository instances. Each operation in the processor will
    create repositories within context managers as needed.
    """
    from app.services.event_bus.bus import get_event_bus
    from app.services.sms.sender import get_sms_sender
    
    sms_sender = await get_sms_sender()
    event_bus = get_event_bus()
    
    return CampaignProcessor(
        sms_sender=sms_sender,
        event_bus=event_bus
    )