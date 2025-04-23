"""
SMS sender service for interacting with the Android SMS Gateway.
"""
import asyncio
import logging
import uuid
import time
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
        metadata: Optional[Dict[str, Any]] = None,
        campaign_id: Optional[str] = None,
        priority: int = 0,
        ttl: Optional[int] = None,
        sim_number: Optional[int] = None,
        is_encrypted: bool = False
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
            campaign_id: Optional campaign ID
            priority: Message priority (0-127, ≥100 bypasses limits)
            ttl: Time-to-live in seconds
            sim_number: SIM card to use (1-3)
            is_encrypted: Whether message is encrypted
            
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
            metadata=metadata or {},
            campaign_id=campaign_id
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
            # Send to gateway - use db_message.id as custom_id for 1:1 mapping
            # This ensures webhooks can easily find the corresponding message
            result = await self._send_to_gateway(
                phone_number=formatted_number,
                message_text=message_text,
                custom_id=db_message.id,  # Use our database ID directly
                priority=priority,
                ttl=ttl,
                sim_number=sim_number,
                is_encrypted=is_encrypted
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
        options: Optional[BatchOptions] = None,
        campaign_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a batch of SMS messages.
        
        Args:
            messages: List of messages to send
            user_id: User ID
            options: Optional batch processing options
            campaign_id: Optional campaign ID
            
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
                campaign_id=campaign_id,
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
        campaign_id: Optional[str] = None,
        options: BatchOptions
    ) -> None:
        """
        Process a batch of messages in background.
        
        Args:
            messages: List of messages to send
            user_id: User ID
            batch_id: Batch ID
            campaign_id: Optional campaign ID
            options: Batch processing options
        """
        processed = 0
        successful = 0
        failed = 0
        
        # Calculate chunk size based on total messages
        # Use smaller chunks for larger batches to avoid overwhelming the system
        total_messages = len(messages)
        if total_messages <= 100:
            chunk_size = 10
        elif total_messages <= 1000:
            chunk_size = 25
        else:
            chunk_size = 50
            
        # Process in chunks for better performance
        for i in range(0, total_messages, chunk_size):
            chunk = messages[i:i+chunk_size]
            
            # Process chunk with concurrent tasks
            tasks = []
            for message in chunk:
                # Create task for each message
                task = asyncio.create_task(
                    self._process_single_message(
                        message=message,
                        user_id=user_id,
                        batch_id=batch_id,
                        campaign_id=campaign_id
                    )
                )
                tasks.append(task)
            
            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for result in results:
                processed += 1
                
                if isinstance(result, Exception):
                    # Failed message
                    failed += 1
                    logger.error(f"Error in batch {batch_id}: {str(result)}")
                    
                    # Stop on first error if configured
                    if options.fail_on_first_error:
                        break
                else:
                    # Successful message
                    successful += 1
            
            # Update batch progress
            await self.message_repository.update_batch_progress(
                batch_id=batch_id,
                increment_processed=len(chunk),
                increment_successful=sum(1 for r in results if not isinstance(r, Exception)),
                increment_failed=sum(1 for r in results if isinstance(r, Exception))
            )
            
            # Check if we should stop due to first error
            if options.fail_on_first_error and failed > 0:
                break
                
            # Delay between chunks to avoid overwhelming gateway
            if i + chunk_size < total_messages:
                await asyncio.sleep(options.delay_between_messages * 2)  # Double delay between chunks
        
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
                "campaign_id": campaign_id,
                "total": len(messages),
                "processed": processed,
                "successful": successful,
                "failed": failed,
                "status": status,
                "user_id": user_id
            }
        )
    
    async def _process_single_message(
        self,
        *,
        message: MessageCreate,
        user_id: str,
        batch_id: Optional[str] = None,
        campaign_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a single message within a batch.
        
        Args:
            message: Message to send
            user_id: User ID
            batch_id: Optional batch ID
            campaign_id: Optional campaign ID
            
        Returns:
            Dict: Result of message processing
            
        Raises:
            Exception: Any error during processing
        """
        # Build metadata
        metadata = {}
        if batch_id:
            metadata["batch_id"] = batch_id
        if campaign_id:
            metadata["campaign_id"] = campaign_id
            
        # Set priority based on campaign
        # Campaigns get slightly higher priority but still below urgent messages
        priority = 50 if campaign_id else 0
            
        # Send message
        return await self.send_message(
            phone_number=message.phone_number,
            message_text=message.message,
            user_id=user_id,
            scheduled_at=message.scheduled_at,
            custom_id=message.custom_id,
            metadata=metadata,
            campaign_id=campaign_id,
            priority=priority
        )
    
    async def send_messages_bulk(
        self,
        *,
        messages: List[Dict[str, Any]],
        user_id: str,
        campaign_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        chunk_size: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Send multiple messages efficiently in bulk.
        
        Args:
            messages: List of message dictionaries with recipient and content
            user_id: User ID
            campaign_id: Optional campaign ID
            batch_id: Optional batch ID
            chunk_size: Number of messages to process in each chunk
            
        Returns:
            List[Dict]: List of results for each message
        """
        results = []
        
        # Process in chunks
        for i in range(0, len(messages), chunk_size):
            chunk = messages[i:i+chunk_size]
            chunk_results = await self._process_message_chunk(
                messages=chunk,
                user_id=user_id,
                campaign_id=campaign_id,
                batch_id=batch_id
            )
            results.extend(chunk_results)
            
            # Small delay between chunks to prevent overloading
            if i + chunk_size < len(messages):
                await asyncio.sleep(1)
        
        return results
    
    async def _process_message_chunk(
        self, 
        messages: List[Dict[str, Any]],
        user_id: str,
        campaign_id: Optional[str] = None,
        batch_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Process a chunk of messages concurrently.
        
        Args:
            messages: List of message dictionaries to process
            user_id: User ID
            campaign_id: Optional campaign ID
            batch_id: Optional batch ID
            
        Returns:
            List[Dict]: Results for each message
        """
        # Create tasks for all messages
        tasks = []
        for msg in messages:
            # Create database entries first to get IDs
            db_message = await self.message_repository.create_message(
                phone_number=msg["phone_number"],
                message_text=msg["message_text"],
                user_id=user_id,
                custom_id=msg.get("custom_id"),
                scheduled_at=msg.get("scheduled_at"),
                metadata=msg.get("metadata", {}),
                campaign_id=campaign_id
            )
            
            # Skip if scheduled for the future
            if db_message.scheduled_at and db_message.scheduled_at > datetime.utcnow():
                tasks.append(asyncio.create_task(
                    asyncio.sleep(0)  # Dummy task for scheduled messages
                ))
                continue
                
            # Create task to send via gateway
            task = asyncio.create_task(
                self._send_message_with_error_handling(
                    db_message=db_message,
                    phone_number=msg["phone_number"],
                    message_text=msg["message_text"],
                    priority=msg.get("priority", 0),
                    ttl=msg.get("ttl"),
                    sim_number=msg.get("sim_number"),
                    is_encrypted=msg.get("is_encrypted", False)
                )
            )
            tasks.append(task)
        
        # Wait for all tasks to complete
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [r for r in results if not isinstance(r, Exception)]
        
        return []
    
    async def _send_message_with_error_handling(
        self,
        *,
        db_message: Any,
        phone_number: str,
        message_text: str,
        priority: int = 0,
        ttl: Optional[int] = None,
        sim_number: Optional[int] = None,
        is_encrypted: bool = False
    ) -> Dict[str, Any]:
        """
        Send message with error handling and status updates.
        
        Args:
            db_message: Database message object
            phone_number: Recipient phone number
            message_text: Message content
            priority: Message priority
            ttl: Time-to-live in seconds
            sim_number: SIM card to use
            is_encrypted: Whether message is encrypted
            
        Returns:
            Dict: Result of message sending
        """
        try:
            # Send to gateway
            result = await self._send_to_gateway(
                phone_number=phone_number,
                message_text=message_text,
                custom_id=db_message.id,  # Use database ID directly
                priority=priority,
                ttl=ttl,
                sim_number=sim_number,
                is_encrypted=is_encrypted
            )
            
            # Update message status
            await self.message_repository.update_message_status(
                message_id=db_message.id,
                status=result.get("status", MessageStatus.PENDING),
                event_type="gateway_response",
                gateway_message_id=result.get("gateway_message_id"),
                data=result
            )
            
            return result
            
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
            
            # Re-raise to be caught by caller
            raise
    
    async def schedule_batch_from_numbers(
        self,
        *,
        phone_numbers: List[str],
        message_text: str,
        user_id: str,
        scheduled_at: Optional[datetime] = None,
        campaign_id: Optional[str] = None
    ) -> str:
        """
        Schedule a batch of messages from a list of phone numbers.
        
        Args:
            phone_numbers: List of phone numbers
            message_text: Message content
            user_id: User ID
            scheduled_at: Optional scheduled delivery time
            campaign_id: Optional campaign ID
            
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
            campaign_id=campaign_id,
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
            campaign_id=filters.get("campaign_id"),  # Support filtering by campaign
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
        custom_id: str,
        priority: int = 0,
        ttl: Optional[int] = None,
        sim_number: Optional[int] = None,
        is_encrypted: bool = False
    ) -> Dict[str, Any]:
        """
        Send message to SMS gateway.
        
        Args:
            phone_number: Recipient phone number
            message_text: Message content
            custom_id: Custom ID for tracking (usually our database ID)
            priority: Message priority (0-127, ≥100 bypasses limits)
            ttl: Time-to-live in seconds
            sim_number: SIM card to use (1-3)
            is_encrypted: Whether message is encrypted
            
        Returns:
            Dict: Gateway response
            
        Raises:
            SMSGatewayError: If there's an error sending the message
            RetryableError: If the error is temporary and can be retried
        """
        # Enforce rate limit (only for non-high-priority messages)
        if priority < 100:
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
                    # Build message with additional parameters
                    message_params = {
                        "id": custom_id,
                        "message": message_text,
                        "phone_numbers": [phone_number],
                        "with_delivery_report": True,
                        "priority": priority
                    }
                    
                    # Add optional parameters if provided
                    if ttl is not None:
                        message_params["ttl"] = ttl
                    if sim_number is not None:
                        message_params["sim_number"] = sim_number
                    if is_encrypted:
                        message_params["is_encrypted"] = True
                    
                    # Create message
                    message = domain.Message(**message_params)
                    
                    # Send message
                    logger.debug(f"Sending to gateway: {phone_number}, message: {message_text[:30]}...")
                    response = await sms_client.send(message)
                    logger.debug(f"Gateway response: {response}")
                    
                    # Check for errors in recipients
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
async def get_sms_sender():
    """Get SMS sender service instance."""
    from app.db.session import get_repository
    from app.db.repositories.messages import MessageRepository
    from app.services.event_bus.bus import get_event_bus
    
    message_repository = await get_repository(MessageRepository)
    event_bus = get_event_bus()
    
    return SMSSender(message_repository, event_bus)