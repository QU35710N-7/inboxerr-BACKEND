# app/db/repositories/campaigns.py
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple
from app.utils.ids import generate_prefixed_id, IDPrefix


from sqlalchemy import select, update, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.campaign import Campaign
from app.models.message import Message


class CampaignRepository(BaseRepository[Campaign, Dict[str, Any], Dict[str, Any]]):
    """Campaign repository for campaign operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize repository with session."""
        super().__init__(session=session, model=Campaign)
    
    async def create_campaign(
        self,
        *,
        name: str,
        user_id: str,
        description: Optional[str] = None,
        scheduled_start_at: Optional[datetime] = None,
        scheduled_end_at: Optional[datetime] = None,
        settings: Optional[Dict[str, Any]] = None
    ) -> Campaign:
        """
        Create a new campaign.
        
        Args:
            name: Campaign name
            user_id: User ID
            description: Optional campaign description
            scheduled_start_at: Optional scheduled start time
            scheduled_end_at: Optional scheduled end time
            settings: Optional campaign settings
            
        Returns:
            Campaign: Created campaign
        """
        campaign_id = generate_prefixed_id(IDPrefix.CAMPAIGN)
        campaign = Campaign(
            id=campaign_id,
            name=name,
            description=description,
            status="draft",
            user_id=user_id,
            scheduled_start_at=scheduled_start_at,
            scheduled_end_at=scheduled_end_at,
            settings=settings or {}
        )
        
        self.session.add(campaign)
        await self.session.commit()
        await self.session.refresh(campaign)
        
        return campaign
    
    async def update_campaign_status(
        self,
        *,
        campaign_id: str,
        status: str,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None
    ) -> Optional[Campaign]:
        """
        Update campaign status with proper transaction handling.
        
        Args:
            campaign_id: Campaign ID
            status: New status
            started_at: Optional start timestamp
            completed_at: Optional completion timestamp
            
        Returns:
            Campaign: Updated campaign or None
        """
        # Start a transaction
        async with self.session.begin():
            # Get campaign
            campaign = await self.get_by_id(campaign_id)
            if not campaign:
                return None
            
            old_status = campaign.status
            campaign.status = status
            
            if started_at:
                campaign.started_at = started_at
            
            if completed_at:
                campaign.completed_at = completed_at
            
            # If status is active and no start time, set it now
            if status == "active" and not campaign.started_at:
                campaign.started_at = datetime.now(timezone.utc)
            
            # If status is completed and no completion time, set it now
            if status in ["completed", "cancelled", "failed"] and not campaign.completed_at:
                campaign.completed_at = datetime.now(timezone.utc)
            
            # Add campaign to session
            self.session.add(campaign)
            
            # If transitioning from draft to active, also update any pending messages
            # that are associated with this campaign
            if old_status == "draft" and status == "active":
                from app.models.message import Message
                from app.schemas.message import MessageStatus
                
                # Update messages
                query = update(Message).where(
                    and_(
                        Message.campaign_id == campaign_id,
                        Message.status == MessageStatus.PENDING,
                        or_(
                            Message.scheduled_at.is_(None),
                            Message.scheduled_at <= datetime.now(timezone.utc)
                        )
                    )
                ).values(
                    status=MessageStatus.PROCESSED
                )
                
                await self.session.execute(query)
            
            # Publish event about status change
            from app.services.event_bus.bus import get_event_bus
            from app.services.event_bus.events import EventType
            
            event_bus = get_event_bus()
            event_type = None
            
            if status == "active":
                event_type = EventType.CAMPAIGN_STARTED
            elif status == "paused":
                event_type = EventType.CAMPAIGN_PAUSED
            elif status == "completed":
                event_type = EventType.CAMPAIGN_COMPLETED
            elif status == "cancelled":
                event_type = EventType.CAMPAIGN_CANCELLED
            elif status == "failed":
                event_type = EventType.CAMPAIGN_FAILED
                
            if event_type:
                await event_bus.publish(
                    event_type,
                    {
                        "campaign_id": campaign_id,
                        "previous_status": old_status,
                        "new_status": status,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                )
            
            # No explicit commit needed - will be committed at the end of the context manager
            await self.session.refresh(campaign)
            
            return campaign

    async def update_campaign_stats(
        self,
        *,
        campaign_id: str,
        increment_sent: int = 0,
        increment_delivered: int = 0,
        increment_failed: int = 0
    ) -> Optional[Campaign]:
        """
        Update campaign statistics.
        
        Args:
            campaign_id: Campaign ID
            increment_sent: Increment sent count
            increment_delivered: Increment delivered count
            increment_failed: Increment failed count
            
        Returns:
            Campaign: Updated campaign or None
        """
        campaign = await self.get_by_id(campaign_id)
        if not campaign:
            return None
        
        # Update counts
        campaign.sent_count += increment_sent
        campaign.delivered_count += increment_delivered
        campaign.failed_count += increment_failed
        
        # Check if campaign is complete
        total_processed = campaign.sent_count + campaign.failed_count
        if total_processed >= campaign.total_messages and campaign.total_messages > 0:
            campaign.status = "completed"
            campaign.completed_at = datetime.now(timezone.utc)
        
        self.session.add(campaign)
        await self.session.commit()
        await self.session.refresh(campaign)
        
        return campaign
    
    async def get_campaigns_for_user(
        self,
        *,
        user_id: str,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[Campaign], int]:
        """
        Get campaigns for a user with optional filtering.
        
        Args:
            user_id: User ID
            status: Optional status filter
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Campaign], int]: List of campaigns and total count
        """
        # Base query
        query = select(Campaign).where(Campaign.user_id == user_id)
        count_query = select(func.count()).select_from(Campaign).where(Campaign.user_id == user_id)
        
        # Apply status filter
        if status:
            query = query.where(Campaign.status == status)
            count_query = count_query.where(Campaign.status == status)
        
        # Order by created_at desc
        query = query.order_by(desc(Campaign.created_at))
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        # Execute queries
        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)
        
        campaigns = result.scalars().all()
        total = count_result.scalar_one()
        
        return campaigns, total
    
    async def add_messages_to_campaign(
        self,
        *,
        campaign_id: str,
        phone_numbers: List[str],
        message_text: str,
        user_id: str
    ) -> int:
        """
        Add messages to a campaign.
        
        Args:
            campaign_id: Campaign ID
            phone_numbers: List of recipient phone numbers
            message_text: Message content
            user_id: User ID
            
        Returns:
            int: Number of messages added
        """
        from app.db.repositories.messages import MessageRepository
        from app.utils.phone import validate_phone
        
        # Get campaign
        campaign = await self.get_by_id(campaign_id)
        if not campaign:
            return 0
        
        # Validate campaign belongs to user
        if campaign.user_id != user_id:
            return 0
        
        # TODO: Implement bulk insertion for better performance
        message_repo = MessageRepository(self.session)
        added_count = 0
        
        for phone in phone_numbers:
            # Basic validation
            is_valid, formatted_number, error, _ = validate_phone(phone)
            if is_valid:
                # Add message to campaign
                await message_repo.create_message(
                    phone_number=formatted_number,
                    message_text=message_text,
                    user_id=user_id,
                    scheduled_at=campaign.scheduled_start_at,
                    metadata={"campaign_id": campaign_id},
                    campaign_id=campaign_id  # Direct link to campaign
                )
                added_count += 1
        
        # Update campaign message count
        if added_count > 0:
            campaign.total_messages += added_count
            self.session.add(campaign)
            await self.session.commit()
        
        return added_count