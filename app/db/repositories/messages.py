"""
Message repository for database operations related to SMS messages.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple
from uuid import uuid4

from sqlalchemy import select, update, delete, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

import logging
from app.utils.ids import generate_prefixed_id, IDPrefix
from app.models.campaign import Campaign
from app.db.repositories.base import BaseRepository
from app.models.message import Message, MessageEvent, MessageBatch, MessageTemplate
from app.schemas.message import MessageCreate, MessageStatus

logger = logging.getLogger("inboxerr.db")

class MessageRepository(BaseRepository[Message, MessageCreate, Dict[str, Any]]):
    """Message repository for database operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize with session and Message model."""
        super().__init__(session=session, model=Message)

    async def create_message(
        self,
        *,
        phone_number: str,
        message_text: str,
        user_id: str,
        custom_id: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        batch_id: Optional[str] = None,
        campaign_id: Optional[str] = None
    ) -> Message:
        """
        Create a new message with related objects in a single transaction.
        """
        # Set initial status based on scheduling
        initial_status = MessageStatus.SCHEDULED if scheduled_at else MessageStatus.PENDING
        
        # Calculate SMS parts
        parts_count = (len(message_text) + 159) // 160  # 160 chars per SMS part
        
        # Generate IDs upfront
        message_id = generate_prefixed_id(IDPrefix.MESSAGE)
        event_id = generate_prefixed_id(IDPrefix.EVENT)
        
        # Create message instance
        message = Message(
            id=message_id,  # Pre-assign ID
            custom_id=custom_id or str(uuid4()),
            phone_number=phone_number,
            message=message_text,
            status=initial_status,
            scheduled_at=scheduled_at,
            user_id=user_id,
            meta_data=metadata or {},
            parts_count=parts_count,
            batch_id=batch_id,
            campaign_id=campaign_id
        )
        
        # Create event instance with pre-assigned message_id
        event = MessageEvent(
            id=event_id,
            message_id=message_id,
            event_type="created",
            status=initial_status,
            data={
                "phone_number": phone_number,
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                "campaign_id": campaign_id
            }
        )
        
        # Begin transaction using savepoint if a transaction is already active
        async with self.session.begin_nested():
            # Add both objects to session
            self.session.add(message)
            self.session.add(event)
            
            # Handle campaign update if needed
            if campaign_id:
                try:
                    # Execute campaign update with direct SQL for better performance in high volume
                    await self.session.execute(
                        update(Campaign)
                        .where(Campaign.id == campaign_id)
                        .values(total_messages=Campaign.total_messages + 1)
                    )
                except Exception as e:
                    # Log but don't fail the message creation if campaign update fails
                    logger.error(f"Error updating campaign {campaign_id} message count: {e}")
        
        # Commit the outer transaction
        await self.session.commit()
        
        # Refresh message
        await self.session.refresh(message)
        
        return message

    async def update_message_status(
        self,
        *,
        message_id: str,
        status: str,
        event_type: str,
        reason: Optional[str] = None,
        gateway_message_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None
    ) -> Optional[Message]:
        """
        Update message status with improved concurrency handling.
        
        Args:
            message_id: Message ID
            status: New status
            event_type: Event type triggering this update
            reason: Optional reason for status change
            gateway_message_id: Optional gateway message ID
            data: Optional additional data
            
        Returns:
            Message: Updated message or None
        """
        # Get the message first to check if it exists
        message = await self.get_by_id(message_id)
        if not message:
            return None
        
        # Update status-specific timestamp
        now = datetime.now(timezone.utc)
        update_data = {
            "status": status,
            "updated_at": now,
        }
        
        if status == MessageStatus.SENT:
            update_data["sent_at"] = now
        elif status == MessageStatus.DELIVERED:
            update_data["delivered_at"] = now
        elif status == MessageStatus.FAILED:
            update_data["failed_at"] = now
            update_data["reason"] = reason
        
        # Set gateway message ID if provided
        if gateway_message_id:
            update_data["gateway_message_id"] = gateway_message_id
        
        # Set a specific savepoint for this operation
        async with self.session.begin_nested() as nested:
            try:
                # Update the message
                await self.session.execute(
                    update(Message)
                    .where(Message.id == message_id)
                    .values(**update_data)
                )
                
                # Create event for status change with unique ID
                event_id = generate_prefixed_id(IDPrefix.EVENT)  # Generate new ID for each event
                event = MessageEvent(
                    id=event_id,
                    message_id=message_id,
                    event_type=event_type,
                    status=status,
                    data=data or {}
                )
                
                # Add event to session
                self.session.add(event)
                
                # Commit the nested transaction
                await nested.commit()
            except Exception as e:
                # Transaction will automatically roll back on exception
                logger.error(f"Error updating message status: {e}")
                return None
        
        # Complete the outer transaction
        await self.session.commit()
        
        # Refresh the message
        await self.session.refresh(message)
        
        return message

    async def create_batch(
        self,
        *,
        user_id: str,
        name: str,
        total: int
    ) -> MessageBatch:
        """
        Create a new message batch.
        
        Args:
            user_id: User ID
            name: Batch name
            total: Total number of messages
            
        Returns:
            MessageBatch: Created batch
        """
        batch_id = generate_prefixed_id(IDPrefix.BATCH)
        batch = MessageBatch(
            id=batch_id,
            name=name,
            total=total,
            processed=0,
            successful=0,
            failed=0,
            status=MessageStatus.PENDING,
            user_id=user_id
        )
        
        async with self.session.begin():
            self.session.add(batch)
        
        await self.session.refresh(batch)
        
        return batch

    async def update_batch_progress(
        self,
        *,
        batch_id: str,
        increment_processed: int = 0,
        increment_successful: int = 0,
        increment_failed: int = 0,
        status: Optional[str] = None
    ) -> Optional[MessageBatch]:
        """
        Update batch progress with proper transaction handling.
        
        Args:
            batch_id: Batch ID
            increment_processed: Increment processed count
            increment_successful: Increment successful count
            increment_failed: Increment failed count
            status: Optional new status
            
        Returns:
            MessageBatch: Updated batch or None
        """
        # For high volume operations, get a fresh session
        from app.db.session import async_session_factory
        
        async with async_session_factory() as fresh_session:
            async with fresh_session.begin():
                # Get batch with SELECT FOR UPDATE to prevent race conditions
                query = select(MessageBatch).where(MessageBatch.id == batch_id)
                result = await fresh_session.execute(query.with_for_update())
                batch = result.scalar_one_or_none()
                
                if not batch:
                    return None
                
                # Update counts
                batch.processed += increment_processed
                batch.successful += increment_successful
                batch.failed += increment_failed
                
                # Update status if provided
                if status:
                    batch.status = status
                    
                # If all messages processed, update status and completion time
                if batch.processed >= batch.total:
                    batch.status = MessageStatus.PROCESSED if batch.failed == 0 else "partial"
                    batch.completed_at = datetime.now(timezone.utc)
                
                # Persist changes
                fresh_session.add(batch)
                # Commit happens automatically at the end of context manager
            
            # Refresh to get updated data
            result = await fresh_session.execute(query)
            updated_batch = result.scalar_one_or_none()
            
            return updated_batch

    async def update_batch_progress_safe(
        self,
        *,
        batch_id: str,
        increment_processed: int = 0,
        increment_successful: int = 0,
        increment_failed: int = 0,
        status: Optional[str] = None
    ) -> Optional[MessageBatch]:
        """
        Update batch progress with proper transaction handling.
        
        Args:
            batch_id: Batch ID
            increment_processed: Increment processed count
            increment_successful: Increment successful count
            increment_failed: Increment failed count
            status: Optional new status
            
        Returns:
            MessageBatch: Updated batch or None
        """
        # Get a completely fresh session for this operation
        from app.db.session import async_session_factory
        
        async with async_session_factory() as fresh_session:
            try:
                async with fresh_session.begin():
                    # Get batch with SELECT FOR UPDATE to prevent race conditions
                    query = select(MessageBatch).where(MessageBatch.id == batch_id)
                    result = await fresh_session.execute(query.with_for_update())
                    batch = result.scalar_one_or_none()
                    
                    if not batch:
                        return None
                    
                    # Update counts
                    batch.processed += increment_processed
                    batch.successful += increment_successful
                    batch.failed += increment_failed
                    
                    # Update status if provided
                    if status:
                        batch.status = status
                        
                    # If all messages processed, update status and completion time
                    if batch.processed >= batch.total:
                        batch.status = MessageStatus.PROCESSED if batch.failed == 0 else "partial"
                        batch.completed_at = datetime.now(timezone.utc)
                    
                    # Add the updated batch to the session
                    fresh_session.add(batch)
                    
                    # Get updated batch after updates are applied
                    # This is automatically refreshed at transaction commit
                    
                # Now outside the transaction, we can safely refresh
                query = select(MessageBatch).where(MessageBatch.id == batch_id)
                result = await fresh_session.execute(query)
                updated_batch = result.scalar_one_or_none()
                    
                return updated_batch
            
            except Exception as e:
                logger.error(f"Error in update_batch_progress_safe: {e}")
                return None
            finally:
                # Explicitly close session to prevent connection leaks
                await fresh_session.close()

    async def get_by_custom_id(self, custom_id: str) -> Optional[Message]:
        """
        Get message by custom ID.
        
        Args:
            custom_id: Custom ID
            
        Returns:
            Message: Found message or None
        """
        return await self.get_by_attribute("custom_id", custom_id)

    async def get_by_gateway_id(self, gateway_id: str) -> Optional[Message]:
        """
        Get message by gateway ID.
        
        Args:
            gateway_id: Gateway message ID
            
        Returns:
            Message: Found message or None
        """
        return await self.get_by_attribute("gateway_message_id", gateway_id)

    async def list_messages_for_user(
        self,
        *,
        user_id: str,
        status: Optional[str] = None,
        phone_number: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        campaign_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[Message], int]:
        """
        List messages for user with filtering.
        
        Args:
            user_id: User ID
            status: Optional status filter
            phone_number: Optional phone number filter
            from_date: Optional from date filter
            to_date: Optional to date filter
            campaign_id: Optional campaign ID filter
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Message], int]: List of messages and total count
        """
        # Base query
        query = select(Message).where(Message.user_id == user_id)
        count_query = select(func.count()).select_from(Message).where(Message.user_id == user_id)
        
        # Apply filters
        if status:
            query = query.where(Message.status == status)
            count_query = count_query.where(Message.status == status)
        
        if phone_number:
            query = query.where(Message.phone_number == phone_number)
            count_query = count_query.where(Message.phone_number == phone_number)

        if campaign_id:
            query = query.where(Message.campaign_id == campaign_id)
            count_query = count_query.where(Message.campaign_id == campaign_id)
        
        if from_date:
            try:
                from_date_obj = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
                query = query.where(Message.created_at >= from_date_obj)
                count_query = count_query.where(Message.created_at >= from_date_obj)
            except ValueError:
                pass
        
        if to_date:
            try:
                to_date_obj = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
                query = query.where(Message.created_at <= to_date_obj)
                count_query = count_query.where(Message.created_at <= to_date_obj)
            except ValueError:
                pass
        
        # Order by created_at desc
        query = query.order_by(desc(Message.created_at))
        
        # Pagination
        query = query.offset(skip).limit(limit)
        
        # Execute queries
        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)
        
        messages = result.scalars().all()
        total = count_result.scalar_one()
        
        return messages, total

    async def get_messages_for_batch(
        self,
        *,
        batch_id: str,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[Message], int]:
        """
        Get messages for a batch.
        
        Args:
            batch_id: Batch ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Message], int]: List of messages and total count
        """
        # Base query
        query = select(Message).where(Message.batch_id == batch_id)
        count_query = select(func.count()).select_from(Message).where(Message.batch_id == batch_id)
        
        # Order by created_at desc
        query = query.order_by(desc(Message.created_at))
        
        # Pagination
        query = query.offset(skip).limit(limit)
        
        # Execute queries
        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)
        
        messages = result.scalars().all()
        total = count_result.scalar_one()
        
        return messages, total
    
    async def get_messages_for_campaign(
        self,
        *,
        campaign_id: str,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[Message], int]:
        """
        Get messages for a campaign.
        
        Args:
            campaign_id: Campaign ID
            status: Optional status filter
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Message], int]: List of messages and total count
        """
        # Base query
        query = select(Message).where(Message.campaign_id == campaign_id)
        count_query = select(func.count()).select_from(Message).where(Message.campaign_id == campaign_id)
        
        # Apply status filter
        if status:
            query = query.where(Message.status == status)
            count_query = count_query.where(Message.status == status)
        
        # Order by created_at desc
        query = query.order_by(desc(Message.created_at))
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        # Execute queries
        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)
        
        messages = result.scalars().all()
        total = count_result.scalar_one()
        
        return messages, total

    async def get_retryable_messages(
        self,
        *,
        max_retries: int = 3,
        limit: int = 50
    ) -> List[Message]:
        """
        Get messages that can be retried.
        
        Args:
            max_retries: Maximum number of retry attempts
            limit: Maximum number of messages to return
            
        Returns:
            List[Message]: List of retryable messages
        """
        # Query for failed messages that can be retried
        query = select(Message).where(
            and_(
                Message.status == MessageStatus.FAILED,
                or_(
                    Message.meta_data.is_(None),  # No metadata at all
                    ~Message.meta_data.contains({"retry_count": max_retries})  # retry_count less than max
                )
            )
        ).order_by(Message.failed_at).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()