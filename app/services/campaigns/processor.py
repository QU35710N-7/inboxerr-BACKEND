# app/services/campaigns/processor.py
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
import uuid

from app.core.config import settings
from app.db.repositories.campaigns import CampaignRepository
from app.db.repositories.messages import MessageRepository
from app.schemas.message import MessageStatus
from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType
from app.services.sms.sender import SMSSender

logger = logging.getLogger("inboxerr.campaigns")

class CampaignProcessor:
    """
    Service for processing SMS campaigns.
    
    Manages campaign execution, chunked processing, and status tracking.
    """
    
    def __init__(
        self,
        campaign_repository: CampaignRepository,
        message_repository: MessageRepository,
        sms_sender: SMSSender,
        event_bus: Any
    ):
        """Initialize campaign processor with required dependencies."""
        self.campaign_repository = campaign_repository
        self.message_repository = message_repository
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
        # Get campaign
        campaign = await self.campaign_repository.get_by_id(campaign_id)
        if not campaign:
            return False
        
        # Validate ownership
        if campaign.user_id != user_id:
            return False
        
        # Check if campaign can be started
        if campaign.status != "draft" and campaign.status != "paused":
            return False
        
        # Update status to active
        updated = await self.campaign_repository.update_campaign_status(
            campaign_id=campaign_id,
            status="active",
            started_at=datetime.utcnow()
        )
        
        if not updated:
            return False
        
        # Start processing in background
        asyncio.create_task(self._process_campaign(campaign_id))
        
        # Publish event
        await self.event_bus.publish(
            EventType.BATCH_CREATED,
            {
                "campaign_id": campaign_id,
                "user_id": user_id,
                "total_messages": campaign.total_messages
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
        # Get campaign
        campaign = await self.campaign_repository.get_by_id(campaign_id)
        if not campaign:
            return False
        
        # Validate ownership
        if campaign.user_id != user_id:
            return False
        
        # Check if campaign can be paused
        if campaign.status != "active":
            return False
        
        # Update status to paused
        updated = await self.campaign_repository.update_campaign_status(
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
        # Get campaign
        campaign = await self.campaign_repository.get_by_id(campaign_id)
        if not campaign:
            return False
        
        # Validate ownership
        if campaign.user_id != user_id:
            return False
        
        # Check if campaign can be cancelled
        if campaign.status in ["completed", "cancelled", "failed"]:
            return False
        
        # Update status to cancelled
        updated = await self.campaign_repository.update_campaign_status(
            campaign_id=campaign_id,
            status="cancelled",
            completed_at=datetime.utcnow()
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
            # Get campaign
            campaign = await self.campaign_repository.get_by_id(campaign_id)
            if not campaign or campaign.status != "active":
                return
            
            # Process in chunks until complete
            async with self._semaphore:
                await self._process_campaign_chunks(campaign)
                
        except Exception as e:
            logger.error(f"Error processing campaign {campaign_id}: {e}", exc_info=True)
            # Update campaign status to failed
            await self.campaign_repository.update_campaign_status(
                campaign_id=campaign_id,
                status="failed",
                completed_at=datetime.utcnow()
            )
        finally:
            # Remove from processing set
            self._processing_campaigns.remove(campaign_id)
    
    async def _process_campaign_chunks(self, campaign) -> None:
        """
        Process campaign messages in chunks.
        
        Args:
            campaign: Campaign object
        """
        # Query pending messages in chunks
        offset = 0
        
        while True:
            # Check if campaign is still active
            campaign = await self.campaign_repository.get_by_id(campaign.id)
            if not campaign or campaign.status != "active":
                logger.info(f"Campaign {campaign.id} is no longer active, stopping processing")
                return
            
            # Get next chunk of messages
            messages, _ = await self.message_repository.get_messages_for_campaign(
                campaign_id=campaign.id,
                status=MessageStatus.PENDING,
                skip=offset,
                limit=self._chunk_size
            )
            
            # If no more messages, campaign is complete
            if not messages:
                logger.info(f"No more pending messages for campaign {campaign.id}")
                await self.campaign_repository.update_campaign_status(
                    campaign_id=campaign.id,
                    status="completed",
                    completed_at=datetime.utcnow()
                )
                return
            
            # Process this chunk
            await self._process_message_chunk(campaign.id, messages)
            
            # Update offset for next chunk
            offset += len(messages)
            
            # Small delay between chunks to avoid overloading
            await asyncio.sleep(0.5)
    
    async def _process_message_chunk(self, campaign_id: str, messages: List[Any]) -> None:
        """
        Process a chunk of messages.
        
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
                # Note: This is not optimal for bulk processing and would be improved in future versions
                result = await self.sms_sender._send_to_gateway(
                    phone_number=message.phone_number,
                    message_text=message.message,
                    custom_id=message.custom_id or str(uuid.uuid4())
                )
                
                # Update message status
                await self.message_repository.update_message_status(
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
                
                # Update message status to failed
                await self.message_repository.update_message_status(
                    message_id=message.id,
                    status=MessageStatus.FAILED,
                    event_type="campaign_process_error",
                    reason=str(e),
                    data={"error": str(e)}
                )
                
                fail_count += 1
        
        # Update campaign stats
        await self.campaign_repository.update_campaign_stats(
            campaign_id=campaign_id,
            increment_sent=success_count,
            increment_failed=fail_count
        )
        
        logger.info(f"Processed chunk for campaign {campaign_id}: {success_count} sent, {fail_count} failed")


# Dependency injection function
async def get_campaign_processor():
    """Get campaign processor service instance."""
    from app.db.session import get_repository
    from app.db.repositories.campaigns import CampaignRepository
    from app.db.repositories.messages import MessageRepository
    from app.services.event_bus.bus import get_event_bus
    from app.services.sms.sender import get_sms_sender
    
    campaign_repository = await get_repository(CampaignRepository)
    message_repository = await get_repository(MessageRepository)
    sms_sender = await get_sms_sender()
    event_bus = get_event_bus()
    
    return CampaignProcessor(
        campaign_repository=campaign_repository,
        message_repository=message_repository,
        sms_sender=sms_sender,
        event_bus=event_bus
    )