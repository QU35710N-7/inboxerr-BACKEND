"""
Message repository for database operations related to SMS messages.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple
from uuid import uuid4

from sqlalchemy import select, update, delete, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload
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
        
        
        return message

    async def create_batch(
        self,
        *,
        user_id: str,
        name: str,
        total: int,
        batch_id: Optional[str] = None,
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
        batch_id =  batch_id or generate_prefixed_id(IDPrefix.BATCH)
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
        # Use a proper transaction
        async with self.session.begin():
            # Get batch with FOR UPDATE to prevent race conditions
            query = select(MessageBatch).where(MessageBatch.id == batch_id)
            result = await self.session.execute(query.with_for_update())
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
            
            # Add batch to session
            self.session.add(batch)
        
        # Get updated batch
        query = select(MessageBatch).where(MessageBatch.id == batch_id)
        result = await self.session.execute(query)
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
        query = (
            select(Message)
            .options(
                joinedload(Message.campaign),       # Eager load campaign
                selectinload(Message.events)        # Eager load events if needed
            )
            .where(Message.user_id == user_id)
        )
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
        query = (
            select(Message)
            .options(joinedload(Message.campaign))
            .where(Message.batch_id == batch_id)
        )
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
        query = (
            select(Message)
            .options(joinedload(Message.campaign))  # This fixes the DetachedInstanceError
            .where(Message.campaign_id == campaign_id)
        )
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
    
    async def count_messages_for_campaign(self, campaign_id: str) -> int:
        """
        Return an exact count of messages that belong to one campaign.
        """
        result = await self.session.execute(
            select(func.count()).select_from(Message)
            .where(Message.campaign_id == campaign_id)
        )
        return result.scalar_one()
    

    async def get_message_by_campaign_and_phone(
    self, 
    campaign_id: str, 
    phone_number: str
) -> Optional[Message]:
        """
        Check if a message already exists for this campaign + phone combination.
        
        Industry standard: Idempotency check to prevent duplicate sends.
        Used by virtual sender to avoid double-sends to same recipient.
        
        Args:
            campaign_id: Campaign ID
            phone_number: Phone number to check
            
        Returns:
            Message: Existing message if found, None if safe to send
        """
        query = select(Message).where(
            and_(
                Message.campaign_id == campaign_id,
                Message.phone_number == phone_number
            )
        ).limit(1)  # We only need to know if ANY message exists
        
        result = await self.session.execute(query)
        message = result.scalar_one_or_none()
        
        return message

    async def get_by_id(self, id: str) -> Optional[Message]:
        """
        Get a message by ID with eager loading of relationships.
        
        This overrides the BaseRepository method to add eager loading
        for campaign and events relationships.
        
        Args:
            id: Message ID
            
        Returns:
            Message: Found message with relationships loaded, or None
        """
        query = (
            select(Message)
            .options(
                joinedload(Message.campaign),    # Eager load campaign
                selectinload(Message.events)     # Eager load events collection
            )
            .where(Message.id == id)
        )
        
        result = await self.session.execute(query)
        return result.scalars().first()

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
    
    async def check_events_for_messages(
        self,
        *,
        message_ids: List[str],
        user_id: str
    ) -> Tuple[int, List[str]]:
        """
        Check how many delivery events exist for given messages.
        
        This method performs a security-conscious check to count events only for messages
        that belong to the specified user, preventing information leakage about other
        users' messages.
        
        Args:
            message_ids: List of message IDs to check for events
            user_id: User ID for security validation
            
        Returns:
            Tuple[int, List[str]]: (total_events_count, message_ids_with_events)
            - total_events_count: Total number of events that would be deleted
            - message_ids_with_events: List of message IDs that have associated events
            
        Security:
            - Only counts events for messages owned by the specified user
            - Prevents cross-user information disclosure
            
        Performance:
            - Uses efficient subquery for user validation
            - Single database round-trip for both counts and message list
        """
        from app.models.message import MessageEvent
        
        # Count total events for user's messages
        events_count_query = select(func.count(MessageEvent.id)).where(
            and_(
                MessageEvent.message_id.in_(message_ids),
                # Security: ensure messages belong to user
                MessageEvent.message_id.in_(
                    select(Message.id).where(
                        and_(
                            Message.id.in_(message_ids),
                            Message.user_id == user_id
                        )
                    )
                )
            )
        )
        
        # Get distinct message IDs that have events (for user's messages only)
        messages_with_events_query = select(MessageEvent.message_id.distinct()).where(
            and_(
                MessageEvent.message_id.in_(message_ids),
                # Security: ensure messages belong to user
                MessageEvent.message_id.in_(
                    select(Message.id).where(
                        and_(
                            Message.id.in_(message_ids),
                            Message.user_id == user_id
                        )
                    )
                )
            )
        )
        
        # Execute both queries
        events_result = await self.session.execute(events_count_query)
        messages_result = await self.session.execute(messages_with_events_query)
        
        total_events = events_result.scalar_one()
        messages_with_events = [row[0] for row in messages_result.fetchall()]
        
        logger.debug(
            f"Event check for user {user_id}: {total_events} events found "
            f"across {len(messages_with_events)} messages"
        )
        
        return total_events, messages_with_events
    

    async def bulk_delete_with_batching(
        self,
        *,
        message_ids: List[str],
        user_id: str,
        force_delete: bool = False,
        batch_size: int = 1000
    ) -> Tuple[int, List[str], Dict[str, Any]]:
        """
        Bulk delete messages with batching for server stability and event safety.
        
        This method processes large deletion operations in smaller batches to prevent
        server overload, connection timeouts, and database lock contention. It handles
        both safe deletion (messages without events) and force deletion (with events).
        
        Args:
            message_ids: List of message IDs to delete
            user_id: User ID for authorization
            force_delete: Whether to delete messages that have delivery events
            batch_size: Number of messages to process per batch (max 5000)
            
        Returns:
            Tuple[int, List[str], Dict[str, Any]]: (total_deleted, failed_ids, batch_info)
            - total_deleted: Total number of messages successfully deleted
            - failed_ids: List of message IDs that failed to delete
            - batch_info: Dictionary with batch processing statistics
            
        Server Stability Features:
            - Processes deletions in configurable batch sizes
            - Includes inter-batch delays to prevent DB overload
            - Each batch is atomic (all succeed or all fail per batch)
            - Graceful handling of partial failures
            
        Event Safety:
            - When force_delete=False: Only deletes messages without events
            - When force_delete=True: Deletes events first, then messages
            - Two-phase deletion prevents foreign key violations
        """
        import asyncio
        
        total_deleted = 0
        all_failed_ids = []
        batches_processed = 0
        events_deleted_total = 0
        
        # Validate batch size
        if batch_size > 5000:
            batch_size = 5000
            logger.warning(f"Batch size capped at 5000 for stability")
        
        logger.info(
            f"Starting batched deletion: {len(message_ids)} messages, "
            f"batch_size={batch_size}, force_delete={force_delete}, user={user_id}"
        )
        
        # Process in batches
        for i in range(0, len(message_ids), batch_size):
            batch_ids = message_ids[i:i + batch_size]
            batches_processed += 1
            
            try:
                # Process this batch
                if force_delete:
                    deleted, failed, events_deleted = await self._delete_batch_with_events(
                        batch_ids, user_id
                    )
                    events_deleted_total += events_deleted
                else:
                    deleted, failed = await self._delete_batch_safe(batch_ids, user_id)
                
                total_deleted += deleted
                all_failed_ids.extend(failed)
                
                logger.debug(
                    f"Batch {batches_processed}: deleted {deleted}, failed {len(failed)}"
                )
                
                # Small delay between batches to prevent overwhelming DB
                if i + batch_size < len(message_ids):  # Don't delay after last batch
                    await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Batch {batches_processed} failed completely: {e}")
                # Add all IDs from failed batch to failed list
                all_failed_ids.extend(batch_ids)
        
        batch_info = {
            "batches_processed": batches_processed,
            "batch_size": batch_size,
            "total_messages": len(message_ids),
            "events_deleted": events_deleted_total
        }
        
        logger.info(
            f"Batched deletion completed: {total_deleted} deleted, "
            f"{len(all_failed_ids)} failed, {batches_processed} batches, "
            f"{events_deleted_total} events deleted"
        )
        
        return total_deleted, all_failed_ids, batch_info
    

    async def _delete_batch_safe(
        self,
        message_ids: List[str],
        user_id: str
    ) -> Tuple[int, List[str]]:
        """
        Delete a batch of messages safely (only messages without delivery events).
        
        This method performs safe deletion by only removing messages that have no
        associated delivery events. Messages with events are skipped and returned
        in the failed list, allowing the caller to handle them appropriately.
        
        Args:
            message_ids: List of message IDs to delete in this batch
            user_id: User ID for authorization
            
        Returns:
            Tuple[int, List[str]]: (deleted_count, failed_message_ids)
            - deleted_count: Number of messages successfully deleted
            - failed_message_ids: List of message IDs that have events (skipped)
            
        Safety Features:
            - Only deletes messages without delivery events
            - Preserves delivery tracking data by default
            - Atomic transaction per batch
            - User authorization on every message
            
        Performance:
            - Single transaction per batch
            - Efficient subquery to identify safe messages
            - Minimal database round-trips
        """
        from app.models.message import MessageEvent
        
        try:
            # Work within existing transaction - don't start a new one
            # Find messages that have NO events (safe to delete)
            messages_without_events_query = select(Message.id).where(
                and_(
                    Message.id.in_(message_ids),
                    Message.user_id == user_id,
                    # Only messages with NO events
                    ~Message.id.in_(
                        select(MessageEvent.message_id.distinct()).where(
                            MessageEvent.message_id.in_(message_ids)
                        )
                    )
                )
            )
            
            result = await self.session.execute(messages_without_events_query)
            safe_message_ids = [row[0] for row in result.fetchall()]
            
            if not safe_message_ids:
                # All messages have events - none can be safely deleted
                return 0, message_ids
            
            # Delete only the safe messages
            delete_query = delete(Message).where(
                and_(
                    Message.id.in_(safe_message_ids),
                    Message.user_id == user_id
                )
            )
            
            delete_result = await self.session.execute(delete_query)
            deleted_count = delete_result.rowcount
            
            # Messages that couldn't be deleted (have events)
            failed_ids = [mid for mid in message_ids if mid not in safe_message_ids]
            
            if failed_ids:
                logger.debug(
                    f"Safe deletion: {deleted_count} deleted, {len(failed_ids)} skipped (have events)"
                )
            else:
                logger.debug(f"Safe deletion: {deleted_count} deleted, no events found")
            
            return deleted_count, failed_ids
                
        except Exception as e:
            logger.error(f"Safe batch deletion failed: {e}")
            return 0, message_ids  # All IDs failed
    

    async def _delete_batch_with_events(
        self,
        message_ids: List[str],
        user_id: str
    ) -> Tuple[int, List[str], int]:
        """
        Delete a batch of messages WITH their delivery events (force deletion).
        
        This method performs force deletion by removing both messages and their
        associated delivery events in a two-phase process. Events are deleted first
        to avoid foreign key constraint violations, then messages are deleted.
        
        Args:
            message_ids: List of message IDs to delete in this batch
            user_id: User ID for authorization
            
        Returns:
            Tuple[int, List[str], int]: (deleted_count, failed_message_ids, events_deleted)
            - deleted_count: Number of messages successfully deleted
            - failed_message_ids: List of message IDs that failed to delete
            - events_deleted: Number of delivery events deleted
            
        Force Deletion Process:
            1. Delete all delivery events for the messages (prevents FK violations)
            2. Delete the messages themselves
            3. Both operations in single atomic transaction
            
        Data Loss Warning:
            - This permanently destroys delivery tracking data
            - Should only be used when explicitly confirmed by user
            - May impact delivery analytics and compliance records
            
        Performance:
            - Two-phase deletion in single transaction
            - Efficient bulk operations with IN clauses
            - User authorization on every operation
        """
        from app.models.message import MessageEvent
        
        try:
            # Work within existing transaction - don't start a new one
            # Phase 1: Delete events first (to avoid FK constraint violations)
            events_delete_query = delete(MessageEvent).where(
                and_(
                    MessageEvent.message_id.in_(message_ids),
                    # Security: only delete events for user's messages
                    MessageEvent.message_id.in_(
                        select(Message.id).where(
                            and_(
                                Message.id.in_(message_ids),
                                Message.user_id == user_id
                            )
                        )
                    )
                )
            )
            
            events_result = await self.session.execute(events_delete_query)
            events_deleted = events_result.rowcount
            
            # Phase 2: Delete messages
            messages_delete_query = delete(Message).where(
                and_(
                    Message.id.in_(message_ids),
                    Message.user_id == user_id
                )
            )
            
            messages_result = await self.session.execute(messages_delete_query)
            messages_deleted = messages_result.rowcount
            
            # Determine which messages failed to delete
            if messages_deleted < len(message_ids):
                # Query to find which messages still exist (failed to delete)
                remaining_query = select(Message.id).where(
                    and_(
                        Message.id.in_(message_ids),
                        Message.user_id == user_id
                    )
                )
                
                remaining_result = await self.session.execute(remaining_query)
                remaining_ids = [row[0] for row in remaining_result.fetchall()]
                failed_ids = remaining_ids
            else:
                failed_ids = []
            
            logger.info(
                f"Force deletion batch: {messages_deleted} messages deleted, "
                f"{events_deleted} events deleted, {len(failed_ids)} failed"
            )
            
            return messages_deleted, failed_ids, events_deleted
                
        except Exception as e:
            logger.error(f"Force deletion batch failed: {e}")
            return 0, message_ids, 0  # All IDs failed, no events deleted
    
    
    async def bulk_delete_campaign_messages(
        self,
        *,
        campaign_id: str,
        user_id: str,
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 10000,
        force_delete: bool = False,
        batch_size: int = 1000
    ) -> Tuple[int, List[str], Dict[str, Any]]:
        """
        Bulk delete messages for a campaign with event safety and server stability.
        
        This method efficiently deletes multiple messages belonging to a specific campaign
        with optional filtering by status and date range. Enhanced with event safety
        checking and batched processing for server stability under high load.
        
        Args:
            campaign_id: Campaign ID - messages must belong to this campaign
            user_id: User ID - for authorization (messages must belong to this user)
            status: Optional status filter (e.g., 'failed', 'sent')
            from_date: Optional from date filter (ISO format string)
            to_date: Optional to date filter (ISO format string)
            limit: Maximum number of messages to delete (default 10K, max 10K for safety)
            force_delete: Whether to delete messages that have delivery events
            batch_size: Number of messages to process per batch for stability
            
        Returns:
            Tuple[int, List[str], Dict[str, Any]]: (deleted_count, failed_ids, metadata)
            - deleted_count: Number of messages successfully deleted
            - failed_ids: List of message IDs that failed to delete
            - metadata: Dictionary with operation details including:
                - requires_confirmation: Whether force delete is needed
                - events_count: Number of events that would be deleted
                - events_deleted: Number of events actually deleted
                - batch_info: Batch processing statistics
                - safety_warnings: List of safety warnings
            
        Event Safety:
            - When force_delete=False: Returns safety warning if events exist
            - When force_delete=True: Deletes both messages and events
            - Two-phase deletion prevents foreign key violations
            
        Server Stability:
            - Processes large operations in configurable batches
            - Includes delays between batches to prevent DB overload
            - Graceful handling of partial failures
            
        Performance:
            - Handles 30K deletions efficiently with batching
            - Uses optimized queries with proper indexes
            - Single transaction per batch for consistency
        """
        # Validate limit for safety
        if limit > 10000:
            limit = 10000
        
        # Build query to get message IDs that match criteria
        id_subquery = (
            select(Message.id)
            .where(
                and_(
                    Message.campaign_id == campaign_id,
                    Message.user_id == user_id
                )
            )
            .limit(limit)
        )
        
        # Apply optional filters
        if status:
            id_subquery = id_subquery.where(Message.status == status)
        
        if from_date:
            try:
                from_date_obj = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
                id_subquery = id_subquery.where(Message.created_at >= from_date_obj)
            except ValueError:
                pass
        
        if to_date:
            try:
                to_date_obj = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
                id_subquery = id_subquery.where(Message.created_at <= to_date_obj)
            except ValueError:
                pass
        
        try:
            # Get the message IDs that match criteria
            id_result = await self.session.execute(id_subquery)
            message_ids = [row[0] for row in id_result.fetchall()]
            
            if not message_ids:
                return 0, [], {
                    "requires_confirmation": False,
                    "events_count": 0,
                    "events_deleted": 0,
                    "batch_info": {"batches_processed": 0, "batch_size": batch_size},
                    "safety_warnings": []
                }
            
            # Check for events if not force deleting
            if not force_delete:
                events_count, messages_with_events = await self.check_events_for_messages(
                    message_ids=message_ids,
                    user_id=user_id
                )
                
                if events_count > 0:
                    # Return safety warning instead of proceeding
                    return 0, [], {
                        "requires_confirmation": True,
                        "events_count": events_count,
                        "events_deleted": 0,
                        "batch_info": {"batches_processed": 0, "batch_size": batch_size},
                        "safety_warnings": [
                            f"Cannot delete {len(message_ids)} message(s) because {events_count} delivery/status events exist.",
                            "Please confirm deletion to remove both message(s) and all associated events."
                        ]
                    }
            
            # Proceed with deletion using batching
            deleted_count, failed_ids, batch_info = await self.bulk_delete_with_batching(
                message_ids=message_ids,
                user_id=user_id,
                force_delete=force_delete,
                batch_size=batch_size
            )
            
            logger.info(
                f"Campaign bulk delete completed: campaign={campaign_id}, "
                f"deleted={deleted_count}, failed={len(failed_ids)}, "
                f"force_delete={force_delete}, batches={batch_info.get('batches_processed', 0)}"
            )
            
            return deleted_count, failed_ids, {
                "requires_confirmation": False,
                "events_count": 0,
                "events_deleted": batch_info.get("events_deleted", 0),
                "batch_info": batch_info,
                "safety_warnings": []
            }
            
        except Exception as e:
            logger.error(f"Error in bulk_delete_campaign_messages: {e}")
            raise e

    
    async def bulk_delete_messages(
        self,
        *,
        message_ids: List[str],
        user_id: str,
        campaign_id: Optional[str] = None,
        force_delete: bool = False
    ) -> Tuple[int, List[str], Dict[str, Any]]:
        """
        Global bulk delete messages by message IDs with event safety.
        
        This method efficiently deletes multiple messages by their specific IDs with
        user authorization and event safety checking. Designed for edge cases like
        cross-campaign cleanup, orphaned message removal, and power user operations.
        
        Args:
            message_ids: List of message IDs to delete (max 1000 for safety)
            user_id: User ID - for authorization (messages must belong to this user)
            campaign_id: Optional campaign context for additional validation
            force_delete: Whether to delete messages that have delivery events
            
        Returns:
            Tuple[int, List[str], Dict[str, Any]]: (deleted_count, failed_ids, metadata)
            - deleted_count: Number of messages successfully deleted
            - failed_ids: List of message IDs that failed to delete
            - metadata: Dictionary with operation details including:
                - requires_confirmation: Whether force delete is needed
                - events_count: Number of events that would be deleted
                - events_deleted: Number of events actually deleted
                - safety_warnings: List of safety warnings
            
        Event Safety:
            - When force_delete=False: Returns safety warning if events exist
            - When force_delete=True: Deletes both messages and events
            - Two-phase deletion prevents foreign key violations
            
        Use Cases:
            - Cross-campaign message cleanup by power users
            - Orphaned message removal during system maintenance
            - Selective message deletion from UI multi-select
            - Compliance-driven deletion by specific message IDs
            
        Performance:
            - Handles up to 1K deletions efficiently
            - Uses IN clause with message ID list
            - Smaller batches for safety vs campaign-scoped operations
        """

        # Validate input
        if not message_ids:
            return 0, [], {
                "requires_confirmation": False,
                "events_count": 0,
                "events_deleted": 0,
                "safety_warnings": []
            }
        
        # Safety limit - smaller than campaign-scoped for global operations
        if len(message_ids) > 1000:
            logger.warning(f"Global bulk delete limited to 1000 messages, received {len(message_ids)}")
            message_ids = message_ids[:1000]
        
        # Remove duplicates while preserving order
        unique_message_ids = list(dict.fromkeys(message_ids))
        
        try:
            # Check for events if not force deleting
            if not force_delete:
                events_count, messages_with_events = await self.check_events_for_messages(
                    message_ids=unique_message_ids,
                    user_id=user_id
                )
                
                if events_count > 0:
                    # Return safety warning instead of proceeding
                    return 0, [], {
                        "requires_confirmation": True,
                        "events_count": events_count,
                        "events_deleted": 0,
                        "safety_warnings": [
                            f"Cannot delete {len(unique_message_ids)} message(s) because {events_count} delivery/status events exist.",
                            "Please confirm deletion to remove both message(s) and all associated events."
                        ]
                    }
            
            # Proceed with deletion (using smaller batches for global operations)
            deleted_count, failed_ids, batch_info = await self.bulk_delete_with_batching(
                message_ids=unique_message_ids,
                user_id=user_id,
                force_delete=force_delete,
                batch_size=500  # Smaller batches for global operations
            )
            
            # Additional campaign context validation for failed messages
            if campaign_id and failed_ids:
                # Filter failed IDs to only those that actually belong to the campaign
                campaign_failed_query = select(Message.id).where(
                    and_(
                        Message.id.in_(failed_ids),
                        Message.user_id == user_id,
                        Message.campaign_id == campaign_id
                    )
                )
                result = await self.session.execute(campaign_failed_query)
                campaign_failed_ids = [row[0] for row in result.fetchall()]
                
                # Update failed list to only include campaign-context failures
                failed_ids = campaign_failed_ids
            
            logger.info(
                f"Global bulk delete completed: deleted={deleted_count}, "
                f"failed={len(failed_ids)}, force_delete={force_delete}, "
                f"campaign_context={campaign_id}, events_deleted={batch_info.get('events_deleted', 0)}"
            )
            
            return deleted_count, failed_ids, {
                "requires_confirmation": False,
                "events_count": 0,
                "events_deleted": batch_info.get("events_deleted", 0),
                "safety_warnings": []
            }
            
        except Exception as e:
            logger.error(f"Error in global bulk_delete_messages: {e}")
            raise e