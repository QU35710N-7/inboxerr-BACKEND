"""
Webhook repository for database operations related to webhooks.
"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from uuid import uuid4

from sqlalchemy import select, update, delete, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.webhook import Webhook, WebhookDelivery, WebhookEvent
from app.core.security import generate_webhook_signing_key


class WebhookRepository(BaseRepository[Webhook, Dict[str, Any], Dict[str, Any]]):
    """Webhook repository for database operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize with session and Webhook model."""
        super().__init__(session=session, model=Webhook)
    
    async def create_webhook(
        self,
        *,
        name: str,
        url: str,
        event_types: List[str],
        user_id: str,
        secret_key: Optional[str] = None,
        gateway_webhook_id: Optional[str] = None
    ) -> Webhook:
        """
        Create a new webhook.
        
        Args:
            name: Webhook name
            url: Webhook URL
            event_types: List of event types to receive
            user_id: User ID
            secret_key: Optional secret key for signature validation
            gateway_webhook_id: Optional gateway webhook ID
            
        Returns:
            Webhook: Created webhook
        """
        # Generate secret key if not provided
        if not secret_key:
            secret_key = generate_webhook_signing_key()
        
        webhook = Webhook(
            id=str(uuid4()),
            name=name,
            url=url,
            event_types=event_types,
            user_id=user_id,
            secret_key=secret_key,
            gateway_webhook_id=gateway_webhook_id,
            is_active=True
        )
        
        self.session.add(webhook)
        await self.session.commit()
        await self.session.refresh(webhook)
        
        return webhook
    
    async def get_webhooks_for_event(
        self,
        *,
        event_type: str,
        user_id: Optional[str] = None
    ) -> List[Webhook]:
        """
        Get webhooks for a specific event type.
        
        Args:
            event_type: Event type
            user_id: Optional user ID to filter webhooks
            
        Returns:
            List[Webhook]: List of matching webhooks
        """
        query = select(Webhook).where(
            and_(
                Webhook.is_active == True,
                Webhook.event_types.contains([event_type])
            )
        )
        
        if user_id:
            query = query.where(Webhook.user_id == user_id)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def create_webhook_delivery(
        self,
        *,
        webhook_id: str,
        event_type: str,
        message_id: Optional[str],
        payload: Dict[str, Any],
        status_code: Optional[int] = None,
        is_success: bool = False,
        error_message: Optional[str] = None,
        retry_count: int = 0
    ) -> WebhookDelivery:
        """
        Record a webhook delivery attempt.
        
        Args:
            webhook_id: Webhook ID
            event_type: Event type
            message_id: Message ID (if applicable)
            payload: Webhook payload
            status_code: HTTP status code
            is_success: Whether delivery was successful
            error_message: Error message if failed
            retry_count: Number of retry attempts
            
        Returns:
            WebhookDelivery: Created webhook delivery record
        """
        delivery = WebhookDelivery(
            id=str(uuid4()),
            webhook_id=webhook_id,
            event_type=event_type,
            message_id=message_id,
            payload=payload,
            status_code=status_code,
            is_success=is_success,
            error_message=error_message,
            retry_count=retry_count
        )
        
        if not is_success and retry_count < 3:  # Configure max retries
            # Schedule next retry with exponential backoff
            backoff = 5 * (2 ** retry_count)  # 5, 10, 20 minutes
            delivery.next_retry_at = datetime.utcnow() + timedelta(minutes=backoff)
        
        self.session.add(delivery)
        await self.session.commit()
        await self.session.refresh(delivery)
        
        # Update webhook stats
        await self._update_webhook_stats(
            webhook_id=webhook_id,
            is_success=is_success,
            last_triggered=datetime.utcnow()
        )
        
        return delivery
    
    async def _update_webhook_stats(
        self,
        *,
        webhook_id: str,
        is_success: bool,
        last_triggered: datetime
    ) -> None:
        """
        Update webhook statistics.
        
        Args:
            webhook_id: Webhook ID
            is_success: Whether delivery was successful
            last_triggered: Timestamp of delivery attempt
        """
        webhook = await self.get_by_id(webhook_id)
        if not webhook:
            return
        
        webhook.last_triggered_at = last_triggered
        
        if is_success:
            webhook.success_count += 1
        else:
            webhook.failure_count += 1
        
        self.session.add(webhook)
        await self.session.commit()
    
    async def get_pending_retries(
        self,
        *,
        limit: int = 10
    ) -> List[WebhookDelivery]:
        """
        Get webhook deliveries pending retry.
        
        Args:
            limit: Maximum number of deliveries to return
            
        Returns:
            List[WebhookDelivery]: List of deliveries pending retry
        """
        now = datetime.utcnow()
        
        query = select(WebhookDelivery).where(
            and_(
                WebhookDelivery.is_success == False,
                WebhookDelivery.next_retry_at <= now,
                WebhookDelivery.next_retry_at.is_not(None),
                WebhookDelivery.retry_count < 3  # Configure max retries
            )
        ).order_by(WebhookDelivery.next_retry_at).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def update_delivery_status(
        self,
        *,
        delivery_id: str,
        status_code: int,
        is_success: bool,
        error_message: Optional[str] = None,
        increment_retry: bool = False
    ) -> Optional[WebhookDelivery]:
        """
        Update webhook delivery status.
        
        Args:
            delivery_id: Delivery ID
            status_code: HTTP status code
            is_success: Whether delivery was successful
            error_message: Error message if failed
            increment_retry: Whether to increment retry count
            
        Returns:
            WebhookDelivery: Updated delivery record or None
        """
        delivery = await self.session.get(WebhookDelivery, delivery_id)
        if not delivery:
            return None
        
        delivery.status_code = status_code
        delivery.is_success = is_success
        delivery.error_message = error_message
        
        if increment_retry:
            delivery.retry_count += 1
        
        if not is_success and delivery.retry_count < 3:  # Configure max retries
            # Schedule next retry with exponential backoff
            backoff = 5 * (2 ** delivery.retry_count)  # 5, 10, 20 minutes
            delivery.next_retry_at = datetime.utcnow() + timedelta(minutes=backoff)
        else:
            delivery.next_retry_at = None
        
        self.session.add(delivery)
        await self.session.commit()
        await self.session.refresh(delivery)
        
        # Update webhook stats
        await self._update_webhook_stats(
            webhook_id=delivery.webhook_id,
            is_success=is_success,
            last_triggered=datetime.utcnow()
        )
        
        return delivery
    
    async def create_webhook_event(
        self,
        *,
        event_type: str,
        payload: Dict[str, Any],
        phone_number: Optional[str] = None,
        message_id: Optional[str] = None,
        gateway_message_id: Optional[str] = None
    ) -> WebhookEvent:
        """
        Record a webhook event received from SMS gateway.
        
        Args:
            event_type: Event type
            payload: Event payload
            phone_number: Phone number
            message_id: Message ID
            gateway_message_id: Gateway message ID
            
        Returns:
            WebhookEvent: Created webhook event
        """
        event = WebhookEvent(
            id=str(uuid4()),
            event_type=event_type,
            phone_number=phone_number,
            message_id=message_id,
            gateway_message_id=gateway_message_id,
            payload=payload,
            processed=False
        )
        
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        
        return event
    
    async def mark_event_processed(
        self,
        *,
        event_id: str,
        error_message: Optional[str] = None
    ) -> Optional[WebhookEvent]:
        """
        Mark a webhook event as processed.
        
        Args:
            event_id: Event ID
            error_message: Optional error message
            
        Returns:
            WebhookEvent: Updated event or None
        """
        event = await self.session.get(WebhookEvent, event_id)
        if not event:
            return None
        
        event.processed = True
        event.error_message = error_message
        
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        
        return event
    
    async def get_unprocessed_events(
        self,
        *,
        limit: int = 10
    ) -> List[WebhookEvent]:
        """
        Get unprocessed webhook events.
        
        Args:
            limit: Maximum number of events to return
            
        Returns:
            List[WebhookEvent]: List of unprocessed events
        """
        query = select(WebhookEvent).where(
            WebhookEvent.processed == False
        ).order_by(WebhookEvent.created_at).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()