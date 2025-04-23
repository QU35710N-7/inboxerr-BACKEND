"""
SMS sender service for interacting with the Android SMS Gateway.
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Union

from app.core.config import settings
from app.core.exceptions import ValidationError, SMSGatewayError, RetryableError
from app.utils.phone import validate_phone
from app.db.repositories.messages import MessageRepository
from app.schemas.message import MessageCreate, MessageStatus, BatchMessageRequest, BatchOptions
from app.services.event_bus.events import EventType

# Lazy import of android_sms_gateway to avoid import errors if not installed
try:
    from android_sms_gateway import client, domain
    SMS_GATEWAY_AVAILABLE = True
except ImportError:
    SMS_GATEWAY_AVAILABLE = False


logger = logging.getLogger("inboxerr.sms")


class SMSSender:
    """
    Service for sending SMS messages through the Android SMS Gateway.
    """
    
    def __init__(
        self,
        message_repository: MessageRepository,
        event_bus: Any
    ):
        """
        Initialize SMS sender service.
        
        Args:
            message_repository: Repository for message storage
            event_bus: Event bus for publishing events
        """
        self.message_repository = message_repository
        self.event_bus = event_bus
        self._semaphore = asyncio.Semaphore(10)  # Limit concurrent requests
        self._last_send_time = 0
        
        # Check if gateway client is available
        if not SMS_GATEWAY_AVAILABLE:
            logger.warning("Android SMS Gateway client not installed. SMS sending will be simulated.")
    
    async def send_message(
        self,
        *,
        phone_number: str,
        message_text: str,
        user_id: str,
        scheduled_at: Optional[datetime] = None,
        custom_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send a single SMS message.
        
        Args:
            phone_number: Recipient phone number
            message_text: Message content
            user_id: User ID
            scheduled_at: Optional scheduled delivery time
            custom_id: Optional custom ID for tracking
            metadata: Optional additional data
            
        Returns:
            Dict: Message details with status
            
        Raises:
            ValidationError: If phone number is invalid
            SMSGatewayError: If there's an error sending the message
        """
        # Validate phone number
        is_valid, formatted_number, error = validate_phone(phone_number)
        if not is_valid:
            raise ValidationError(message=f"Invalid phone number: {error}")
        
        # Create message in database
        db_message = await self.message_repository.create_message(
            phone_number=formatted_number,
            message_text=message_text,
            user_id=user_id,
            custom_id=custom_id,
            scheduled_at=scheduled_at,
            metadata=metadata
        )
        
        # If scheduled for future, return message details
        if scheduled_at and scheduled_at > datetime.utcnow():
            logger.info(f"Message {db_message.id} scheduled for {scheduled_at}")
            
            # Publish event
            await self.event_bus.publish(
                EventType.MESSAGE_SCHEDULED,
                {
                    "message_id": db_message.id,
                    "phone_number": formatted_number,
                    "scheduled_at": scheduled_at.isoformat(),
                    "user_id": user_id
                }
            )
            
            return db_message.dict()
        
        # Otherwise, send immediately
        try:
            # Send to gateway
            result = await self._send_to_gateway(
                phone_number=formatted_number,
                message_text=message_text,
                custom_id=db_message.custom_id
            )
            
            # Update message status
            await self.message_repository.update_message_status(
                message_id=db_message.id,
                status=result.get("status", MessageStatus.PENDING),
                event_type="gateway_response",
                gateway_message_id=result.get("gateway_message_id"),
                data=result
            )
            
            # Get updated message
            updated_message = await self.message_repository.get_by_id(db_message.id)
            return updated_message.dict()
            
        except Exception as e:
            # Handle error
            error_status = MessageStatus.FAILED
            error_message = str(e)
            logger.error(f"Error sending message {db_message.id}: {error_message}")
            
            # Update message status
            await self.message_repository.update_message_status(
                message_id=db_message.id,
                status=error_status,
                event_type="send_error",
                reason=error_message,
                data={"error": error_message}
            )
            
            # Re-raise as SMSGatewayError
            if isinstance(e, RetryableError):
                raise SMSGatewayError(message=error_message, code="GATEWAY_ERROR", status_code=503)
            else:
                raise SMSGatewayError(message=error_message, code="GATEWAY_ERROR")
    
    async def send_batch(
        self,
        *,
        messages: List[MessageCreate],
        user_id: str,
        options: Optional[BatchOptions] = None
    ) -> Dict[str, Any]:
        """
        Send a batch of SMS messages.
        
        Args:
            messages: List of messages to send
            user_id: User ID
            options: Optional batch processing options
            
        Returns:
            Dict: Batch details with status
            
        Raises:
            ValidationError: If any phone number is invalid
            SMSGatewayError: If there's an error sending the messages
        """
        if not messages:
            raise ValidationError(message="No messages provided")
        
        # Set default options
        if not options:
            options = BatchOptions(
                delay_between_messages=0.3,
                fail_on_first_error=False,
                retry_failed=True
            )
        
        # Create batch in database
        batch = await self.message_repository.create_batch(
            user_id=user_id,
            name=f"Batch {datetime.utcnow().isoformat()}",
            total=len(messages)
        )
        
        # Process in background
        asyncio.create_task(
            self._process_batch(
                messages=messages,
                user_id=user_id,
                batch_id=batch.id,
                options=options
            )
        )
        
        # Return batch details
        return {
            "batch_id": batch.id,
            "total": batch.total,
            "processed": 0,
            "successful": 0,
            "failed": 0,
            "status": batch.status,
            "created_at": batch.created_at
        }
    
    async def _process_batch(
        self,
        *,
        messages: List[MessageCreate],
        user_id: str,
        batch_id: str,
        options: BatchOptions
    ) -> None:
        """
        Process a batch of messages in background.
        
        Args:
            messages: List of messages to send
            user_id: User ID
            batch_id: Batch ID
            options: Batch processing options
        """
        processed = 0
        successful = 0
        failed = 0
        
        for message in messages:
            try:
                # Send message
                await self.send_message(
                    phone_number=message.phone_number,
                    message_text=message.message,
                    user_id=user_id,
                    scheduled_at=message.scheduled_at,
                    custom_id=message.custom_id,
                    metadata={"batch_id": batch_id}
                )
                
                # Update counters
                processed += 1
                successful += 1
                
            except Exception as e:
                # Update counters
                processed += 1
                failed += 1
                
                logger.error(f"Error in batch {batch_id}: {str(e)}")
                
                # Stop on first error if configured
                if options.fail_on_first_error:
                    break
            
            # Update batch progress
            await self.message_repository.update_batch_progress(
                batch_id=batch_id,
                increment_processed=1,
                increment_successful=1 if failed == 0 else 0,
                increment_failed=1 if failed > 0 else 0
            )
            
            # Delay between messages
            if options.delay_between_messages > 0:
                await asyncio.sleep(options.delay_between_messages)
        
        # Update batch status
        status = MessageStatus.PROCESSED
        if processed == 0:
            status = MessageStatus.FAILED
        elif failed > 0:
            status = "partial"
        
        await self.message_repository.update_batch_progress(
            batch_id=batch_id,
            status=status
        )
        
        # Publish event
        await self.event_bus.publish(
            EventType.BATCH_COMPLETED,
            {
                "batch_id": batch_id,
                "total": len(messages),
                "processed": processed,
                "successful": successful,
                "failed": failed,
                "status": status,
                "user_id": user_id
            }
        )
    
    async def schedule_batch_from_numbers(
        self,
        *,
        phone_numbers: List[str],
        message_text: str,
        user_id: str,
        scheduled_at: Optional[datetime] = None
    ) -> str:
        """
        Schedule a batch of messages from a list of phone numbers.
        
        Args:
            phone_numbers: List of phone numbers
            message_text: Message content
            user_id: User ID
            scheduled_at: Optional scheduled delivery time
            
        Returns:
            str: Batch ID
            
        Raises:
            ValidationError: If any phone number is invalid
        """
        if not phone_numbers:
            raise ValidationError(message="No phone numbers provided")
        
        # Create messages
        messages = []
        for phone in phone_numbers:
            # Basic validation
            is_valid, formatted_number, error = validate_phone(phone)
            if is_valid:
                messages.append(
                    MessageCreate(
                        phone_number=formatted_number,
                        message=message_text,
                        scheduled_at=scheduled_at,
                        custom_id=str(uuid.uuid4())
                    )
                )
        
        if not messages:
            raise ValidationError(message="No valid phone numbers found")
        
        # Create and process batch
        result = await self.send_batch(
            messages=messages,
            user_id=user_id,
            options=BatchOptions(
                delay_between_messages=settings.DELAY_BETWEEN_SMS,
                fail_on_first_error=False,
                retry_failed=True
            )
        )
        
        return result["batch_id"]
    
    async def get_message(self, message_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get message details.
        
        Args:
            message_id: Message ID or custom ID
            user_id: User ID for authorization
            
        Returns:
            Dict: Message details or None if not found
        """
        # Try to get by ID first
        message = await self.message_repository.get_by_id(message_id)
        
        # If not found, try custom ID
        if not message:
            message = await self.message_repository.get_by_custom_id(message_id)
            
        # If not found, try gateway ID
        if not message:
            message = await self.message_repository.get_by_gateway_id(message_id)
        
        # Check authorization
        if message and str(message.user_id) != str(user_id):
            return None
        
        return message.dict() if message else None
    
    async def list_messages(
        self,
        *,
        filters: Dict[str, Any],
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        List messages with filtering and pagination.
        
        Args:
            filters: Filter criteria
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Dict], int]: List of messages and total count
        """
        # Extract user_id from filters
        user_id = filters.pop("user_id", None)
        if not user_id:
            return [], 0
        
        # Get messages
        messages, total = await self.message_repository.list_messages_for_user(
            user_id=user_id,
            status=filters.get("status"),
            phone_number=filters.get("phone_number"),
            from_date=filters.get("from_date"),
            to_date=filters.get("to_date"),
            skip=skip,
            limit=limit
        )
        
        # Convert to dict
        message_dicts = [message.dict() for message in messages]
        
        return message_dicts, total
    
    async def update_message_status(
        self,
        *,
        message_id: str,
        status: str,
        reason: Optional[str] = None,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Update message status.
        
        Args:
            message_id: Message ID
            status: New status
            reason: Reason for status change
            user_id: User ID for authorization
            
        Returns:
            Dict: Updated message or None if not found
        """
        # Get message
        message = await self.message_repository.get_by_id(message_id)
        if not message:
            return None
        
        # Check authorization
        if str(message.user_id) != str(user_id):
            return None
        
        # Update status
        updated = await self.message_repository.update_message_status(
            message_id=message_id,
            status=status,
            event_type="manual_update",
            reason=reason,
            data={"updated_by": user_id}
        )
        
        return updated.dict() if updated else None
    
    async def delete_message(self, message_id: str, user_id: str) -> bool:
        """
        Delete a message.
        
        Args:
            message_id: Message ID
            user_id: User ID for authorization
            
        Returns:
            bool: True if deleted, False otherwise
        """
        # Get message
        message = await self.message_repository.get_by_id(message_id)
        if not message:
            return False
        
        # Check authorization
        if str(message.user_id) != str(user_id):
            return False
        
        # Delete message
        return await self.message_repository.delete(id=message_id)
    
    async def get_task_status(self, task_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a background task (batch).
        
        Args:
            task_id: Task/batch ID
            user_id: User ID for authorization
            
        Returns:
            Dict: Task status or None if not found
        """
        # Get batch
        batch = await self.message_repository.get_by_id(task_id)
        if not batch:
            return None
        
        # Check authorization
        if str(batch.user_id) != str(user_id):
            return None
        
        # Get message stats
        messages, total = await self.message_repository.get_messages_for_batch(
            batch_id=task_id,
            limit=5  # Just get the first few for preview
        )
        
        # Convert to dict
        message_previews = [message.dict() for message in messages]
        
        return {
            "id": batch.id,
            "status": batch.status,
            "total": batch.total,
            "processed": batch.processed,
            "successful": batch.successful,
            "failed": batch.failed,
            "created_at": batch.created_at,
            "completed_at": batch.completed_at,
            "message_previews": message_previews
        }
    
    async def _send_to_gateway(
        self,
        *,
        phone_number: str,
        message_text: str,
        custom_id: str
    ) -> Dict[str, Any]:
        """
        Send message to SMS gateway.
        
        Args:
            phone_number: Recipient phone number
            message_text: Message content
            custom_id: Custom ID for tracking
            
        Returns:
            Dict: Gateway response
            
        Raises:
            SMSGatewayError: If there's an error sending the message
            RetryableError: If the error is temporary and can be retried
        """
        # Enforce rate limit
        await self._enforce_rate_limit()
        
        # Check if gateway client is available
        if not SMS_GATEWAY_AVAILABLE:
            # Simulate sending for development
            logger.warning("Simulating SMS send to %s: %s", phone_number, message_text[:30])
            await asyncio.sleep(0.5)  # Simulate API delay
            
            # Return simulated response
            return {
                "status": MessageStatus.SENT,
                "gateway_message_id": f"sim_{uuid.uuid4()}",
                "phone_number": phone_number,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # Use semaphore to limit concurrent requests
        async with self._semaphore:
            try:
                # Create client
                async with client.AsyncAPIClient(
                    login=settings.SMS_GATEWAY_LOGIN,
                    password=settings.SMS_GATEWAY_PASSWORD,
                    base_url=settings.SMS_GATEWAY_URL
                ) as sms_client:
                    # Create message
                    message = domain.Message(
                        id=custom_id,
                        message=message_text,
                        phone_numbers=[phone_number],
                        with_delivery_report=True
                    )
                    
                    # Send message
                    logger.debug(f"Sending to gateway: {phone_number}, message: {message_text[:30]}...")
                    response = await sms_client.send(message)
                    logger.debug(f"Gateway response: {response}")
                    
                    # Check for errors
                    recipient_state = response.recipients[0] if response.recipients else None
                    if recipient_state and recipient_state.error:
                        raise SMSGatewayError(message=recipient_state.error)
                    
                    # Extract status
                    status = str(response.state.value).lower() if hasattr(response, 'state') else MessageStatus.PENDING
                    gateway_id = getattr(response, 'id', None)
                    
                    # Return result
                    return {
                        "status": status,
                        "gateway_message_id": gateway_id,
                        "phone_number": phone_number,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    
            except client.ClientError as e:
                logger.error(f"SMS gateway client error: {str(e)}")
                raise RetryableError(
                    message=f"SMS gateway client error: {str(e)}",
                    retry_after=30.0
                )
            except Exception as e:
                logger.error(f"SMS gateway error: {str(e)}")
                raise SMSGatewayError(message=f"SMS gateway error: {str(e)}")
    
    async def _enforce_rate_limit(self) -> None:
        """
        Enforce rate limiting for SMS sending.
        
        Adds dynamic delay based on settings.DELAY_BETWEEN_SMS.
        """
        current_time = asyncio.get_event_loop().time()
        elapsed = current_time - self._last_send_time
        remaining_delay = max(0, settings.DELAY_BETWEEN_SMS - elapsed)
        
        if remaining_delay > 0:
            await asyncio.sleep(remaining_delay)
        
        self._last_send_time = asyncio.get_event_loop().time()


# Dependency injection function
def get_sms_sender():
    """Get SMS sender service instance."""
    from app.db.session import get_repository
    from app.db.repositories.messages import MessageRepository
    from app.services.event_bus.bus import get_event_bus
    
    message_repository = get_repository(MessageRepository)
    event_bus = get_event_bus()
    
    return SMSSender(message_repository, event_bus)