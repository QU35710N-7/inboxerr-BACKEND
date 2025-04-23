"""
Message repository for database operations related to SMS messages.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from uuid import uuid4

from sqlalchemy import select, update, delete, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.message import Message, MessageEvent, MessageBatch, MessageTemplate
from app.schemas.message import MessageCreate, MessageStatus


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
        batch_id: Optional[str] = None
    ) -> Message:
        """
        Create a new message.
        
        Args:
            phone_number: Recipient phone number
            message_text: Message content
            user_id: User who is sending the message
            custom_id: Optional custom ID for tracking
            scheduled_at: Optional scheduled time
            metadata: Optional additional data
            batch_id: Optional batch ID
            
        Returns:
            Message: Created message
        """
        # Set initial status based on scheduling
        initial_status = MessageStatus.SCHEDULED if scheduled_at else MessageStatus.PENDING
        
        # Calculate SMS parts (simple calculation, can be improved)
        parts_count = (len(message_text) + 159) // 160  # 160 chars per SMS part, rounded up
        
        # Create message
        message = Message(
            id=str(uuid4()),
            custom_id=custom_id or str(uuid4()),
            phone_number=phone_number,
            message=message_text,
            status=initial_status,
            scheduled_at=scheduled_at,
            user_id=user_id,
            metadata=metadata or {},
            parts_count=parts_count,
            batch_id=batch_id
        )
        
        self.session.add(message)
        
        # Create initial event
        event = MessageEvent(
            id=str(uuid4()),
            message_id=message.id,
            event_type="created",
            status=initial_status,
            data={
                "phone_number": phone_number,
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None
            }
        )
        
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(message)
        
        return message
    
    async def update_message_status(
        self,
        *,
        message_id: str,
        status: MessageStatus,
        event_type: str,
        reason: Optional[str] = None,
        gateway_message_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None
    ) -> Optional[Message]:
        """
        Update message status and add an event.
        
        Args:
            message_id: Message ID
            status: New status
            event_type: Event type
            reason: Optional reason for status change
            gateway_message_id: Optional gateway message ID
            data: Optional additional data
            
        Returns:
            Message: Updated message or None
        """
        # Get message
        message = await self.get_by_id(message_id)
        if not message:
            return None
        
        # Update fields based on status
        update_fields = {"status": status}
        
        # Update timestamp based on status
        now = datetime.utcnow()
        if status == MessageStatus.SENT:
            update_fields["sent_at"] = now
        elif status == MessageStatus.DELIVERED:
            update_fields["delivered_at"] = now
        elif status == MessageStatus.FAILED:
            update_fields["failed_at"] = now
            update_fields["reason"] = reason
        
        # Update gateway message ID if provided
        if gateway_message_id:
            update_fields["gateway_message_id"] = gateway_message_id
        
        # Create event data combining provided data and status-specific fields
        event_data = data or {}
        event_data.update({
            "status": status,
            "timestamp": now.isoformat(),
            "reason": reason
        })
        
        # Create event
        event = MessageEvent(
            id=str(uuid4()),
            message_id=message_id,
            event_type=event_type,
            status=status,
            data=event_data
        )
        
        # Update message
        for field, value in update_fields.items():
            setattr(message, field, value)
        
        # Save changes
        self.session.add(message)
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(message)
        
        return message
    
    async def get_message_with_events(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Get message with all its events.
        
        Args:
            message_id: Message ID
            
        Returns:
            Dict: Message with events or None
        """
        message = await self.get_by_id(message_id)
        if not message:
            return None
        
        # Get events
        query = select(MessageEvent).where(MessageEvent.message_id == message_id).order_by(MessageEvent.created_at)
        result = await self.session.execute(query)
        events = result.scalars().all()
        
        # Convert to dict
        message_dict = message.dict()
        message_dict["events"] = [event.dict() for event in events]
        
        return message_dict
    
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
        Get message by gateway message ID.
        
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
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[Message], int]:
        """
        List messages for a user with filtering and pagination.
        
        Args:
            user_id: User ID
            status: Optional status filter
            phone_number: Optional phone number filter
            from_date: Optional start date filter
            to_date: Optional end date filter
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Message], int]: List of messages and total count
        """
        # Base query
        query = select(Message).where(Message.user_id == user_id)
        
        # Apply filters
        if status:
            query = query.where(Message.status == status)
        
        if phone_number:
            query = query.where(Message.phone_number == phone_number)
        
        if from_date:
            query = query.where(Message.created_at >= from_date)
        
        if to_date:
            query = query.where(Message.created_at <= to_date)
        
        # Get total count before pagination
        count_query = select(func.count()).select_from(Message).where(query.whereclause)
        count_result = await self.session.execute(count_query)
        total_count = count_result.scalar_one()
        
        # Apply sorting and pagination
        query = query.order_by(desc(Message.created_at)).offset(skip).limit(limit)
        
        # Execute query
        result = await self.session.execute(query)
        messages = result.scalars().all()
        
        return messages, total_count
    
    async def create_batch(
        self,
        *,
        user_id: str,
        name: Optional[str] = None,
        total: int = 0
    ) -> MessageBatch:
        """
        Create a new message batch.
        
        Args:
            user_id: User ID
            name: Optional batch name
            total: Total number of messages in batch
            
        Returns:
            MessageBatch: Created batch
        """
        batch = MessageBatch(
            id=str(uuid4()),
            name=name,
            total=total,
            processed=0,
            successful=0,
            failed=0,
            status=MessageStatus.PENDING,
            user_id=user_id
        )
        
        self.session.add(batch)
        await self.session.commit()
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
        Update batch progress.
        
        Args:
            batch_id: Batch ID
            increment_processed: Number to increment processed count
            increment_successful: Number to increment successful count
            increment_failed: Number to increment failed count
            status: Optional new status
            
        Returns:
            MessageBatch: Updated batch or None
        """
        # Get batch
        query = select(MessageBatch).where(MessageBatch.id == batch_id)
        result = await self.session.execute(query)
        batch = result.scalar_one_or_none()
        
        if not batch:
            return None
        
        # Update fields
        batch.processed += increment_processed
        batch.successful += increment_successful
        batch.failed += increment_failed
        
        # Update status if provided
        if status:
            batch.status = status
            
        # Mark as completed if all messages are processed
        if batch.processed >= batch.total:
            batch.status = MessageStatus.PROCESSED
            batch.completed_at = datetime.utcnow()
        
        # Save changes
        self.session.add(batch)
        await self.session.commit()
        await self.session.refresh(batch)
        
        return batch
    
    async def get_scheduled_messages(
        self,
        *,
        limit: int = 100
    ) -> List[Message]:
        """
        Get messages scheduled for sending.
        
        Args:
            limit: Maximum number of messages to return
            
        Returns:
            List[Message]: List of scheduled messages
        """
        now = datetime.utcnow()
        
        query = select(Message).where(
            and_(
                Message.status == MessageStatus.SCHEDULED,
                Message.scheduled_at <= now
            )
        ).order_by(Message.scheduled_at).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_messages_for_batch(
        self,
        *,
        batch_id: str,
        skip: int = 0,
        limit: int = 100
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
        
        # Get total count before pagination
        count_query = select(func.count()).select_from(Message).where(Message.batch_id == batch_id)
        count_result = await self.session.execute(count_query)
        total_count = count_result.scalar_one()
        
        # Apply sorting and pagination
        query = query.order_by(desc(Message.created_at)).offset(skip).limit(limit)
        
        # Execute query
        result = await self.session.execute(query)
        messages = result.scalars().all()
        
        return messages, total_count
    
    async def create_template(
        self,
        *,
        name: str,
        content: str,
        user_id: str,
        description: Optional[str] = None,
        variables: Optional[List[str]] = None
    ) -> MessageTemplate:
        """
        Create a message template.
        
        Args:
            name: Template name
            content: Template content
            user_id: User ID
            description: Optional description
            variables: Optional list of variables
            
        Returns:
            MessageTemplate: Created template
        """
        template = MessageTemplate(
            id=str(uuid4()),
            name=name,
            content=content,
            description=description,
            is_active=True,
            user_id=user_id,
            variables=variables or []
        )
        
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        
        return template
    
    async def get_templates_for_user(
        self,
        *,
        user_id: str,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[MessageTemplate], int]:
        """
        Get message templates for a user.
        
        Args:
            user_id: User ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[MessageTemplate], int]: List of templates and total count
        """
        # Base query
        query = select(MessageTemplate).where(
            and_(
                MessageTemplate.user_id == user_id,
                MessageTemplate.is_active == True
            )
        )
        
        # Get total count before pagination
        count_query = select(func.count()).select_from(MessageTemplate).where(
            and_(
                MessageTemplate.user_id == user_id,
                MessageTemplate.is_active == True
            )
        )
        count_result = await self.session.execute(count_query)
        total_count = count_result.scalar_one()
        
        # Apply sorting and pagination
        query = query.order_by(MessageTemplate.name).offset(skip).limit(limit)
        
        # Execute query
        result = await self.session.execute(query)
        templates = result.scalars().all()
        
        return templates, total_count