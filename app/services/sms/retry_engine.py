# app/services/sms/retry_engine.py
import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from app.core.config import settings
from app.db.repositories.messages import MessageRepository
from app.schemas.message import MessageStatus
from app.services.event_bus.events import EventType
from app.services.event_bus.bus import get_event_bus
from app.core.exceptions import SMSGatewayError, RetryableError

logger = logging.getLogger("inboxerr.retry")

class RetryEngine:
    """
    Service for retrying failed messages.
    
    Periodically checks for failed messages and attempts to resend them.
    """
    
    def __init__(self, message_repository: MessageRepository, event_bus: Any, sms_sender: Any):
        """
        Initialize retry engine with required dependencies.
        
        Args:
            message_repository: Repository for message access
            event_bus: Event bus for publishing events
            sms_sender: SMS sender service for resending messages
        """
        self.message_repository = message_repository
        self.event_bus = event_bus
        self.sms_sender = sms_sender
        self._running = False
        self._semaphore = asyncio.Semaphore(5)  # Limit concurrent retries
        
    async def start(self) -> None:
        """Start the retry engine."""
        if self._running:
            return
            
        self._running = True
        logger.info("Starting retry engine")
        
        while self._running:
            try:
                await self._process_retries()
            except Exception as e:
                logger.error(f"Error in retry engine: {e}", exc_info=True)
                
            # Wait before next cycle
            await asyncio.sleep(settings.RETRY_INTERVAL_SECONDS)
            
    async def stop(self) -> None:
        """Stop the retry engine."""
        self._running = False
        logger.info("Retry engine stopped")
    
    async def _process_retries(self) -> None:
        """Process messages pending retry."""
        # Get messages that need retry
        retry_candidates = await self._get_retry_candidates()
        
        if not retry_candidates:
            logger.debug("No messages to retry")
            return
            
        logger.info(f"Found {len(retry_candidates)} messages to retry")
        
        # Process retries with concurrency limit
        tasks = []
        for message in retry_candidates:
            tasks.append(self._retry_message(message))
            
        if tasks:
            # Process retries concurrently but with limit
            for i in range(0, len(tasks), 5):  # Process in chunks of 5
                chunk = tasks[i:i+5]
                await asyncio.gather(*chunk)
                await asyncio.sleep(1)  # Short delay between chunks
    
    async def _get_retry_candidates(self) -> List[Dict[str, Any]]:
        """
        Get messages that are candidates for retry.
        
        Returns:
            List[Dict]: List of messages that should be retried
        """
        # Parameters for retry candidate selection
        now = datetime.utcnow()
        max_retries = settings.RETRY_MAX_ATTEMPTS
        
        try:
            # Query for messages that:
            # 1. Are in a failed state
            # 2. Have not exceeded max retry attempts
            # 3. Have a retryable error or no error specified
            # 4. Last retry attempt was long enough ago (based on exponential backoff)
            
            # This is a simplified implementation - in production you might want
            # more sophisticated filtering and prioritization
            failed_messages = await self.message_repository.get_retryable_messages(
                max_retries=max_retries,
                limit=50  # Limit number of messages to process in one cycle
            )
            
            # Filter messages based on retry delay (exponential backoff)
            retry_candidates = []
            
            for message in failed_messages:
                # Get retry attempt count (from message metadata or events)
                retry_count = self._get_retry_count(message)
                
                # Calculate backoff delay - 30s, 2m, 8m, 30m, 2h, etc.
                backoff_seconds = 30 * (2 ** retry_count)
                
                # Get timestamp of last attempt
                last_attempt = message.failed_at or message.updated_at
                
                # Check if enough time has passed for retry
                if now - last_attempt > timedelta(seconds=backoff_seconds):
                    retry_candidates.append(message)
            
            return retry_candidates
        
        except Exception as e:
            logger.error(f"Error getting retry candidates: {e}", exc_info=True)
            # Return empty list on error to prevent crashing the retry engine
            return []
    
    
    def _get_retry_count(self, message: Any) -> int:
        """
        Get the retry count for a message.
        
        Args:
            message: Message object
            
        Returns:
            int: Number of retry attempts
        """
        # Check if retry count is in metadata
        meta_data = getattr(message, 'meta_data', {}) or {}
        if isinstance(meta_data, dict) and 'retry_count' in meta_data:
            return meta_data.get('retry_count', 0)
            
        # Fallback - count events of type "retry"
        retry_events = [e for e in getattr(message, 'events', []) 
                       if getattr(e, 'event_type', '') == 'retry']
        return len(retry_events)
    
    async def _retry_message(self, message: Any) -> None:
        """
        Retry sending a message.
        
        Args:
            message: Message to retry
        """
        message_id = message.id
        phone_number = message.phone_number
        message_text = message.message
        custom_id = message.custom_id
        
        # Get current retry count
        retry_count = self._get_retry_count(message)
        
        try:
            logger.info(f"Retrying message {message_id} (attempt {retry_count + 1})")
            
            # Use semaphore to limit concurrent retries
            async with self._semaphore:
                # Reset status to pending for retry
                await self.message_repository.update_message_status(
                    message_id=message_id,
                    status=MessageStatus.PENDING,
                    event_type="retry",
                    data={
                        "retry_count": retry_count + 1,
                        "previous_error": message.reason
                    }
                )
                
                # Update metadata to track retry count
                meta_data = getattr(message, 'meta_data', {}) or {}
                if isinstance(meta_data, dict):
                    meta_data['retry_count'] = retry_count + 1
                    await self.message_repository.update(
                        id=message_id,
                        obj_in={"meta_data": meta_data}
                    )
                
                # Attempt to send again
                result = await self.sms_sender._send_to_gateway(
                    phone_number=phone_number,
                    message_text=message_text,
                    custom_id=custom_id or str(uuid.uuid4())
                )
                
                # Update message status
                await self.message_repository.update_message_status(
                    message_id=message_id,
                    status=result.get("status", MessageStatus.PENDING),
                    event_type="retry_success",
                    gateway_message_id=result.get("gateway_message_id"),
                    data=result
                )
                
                # Publish event
                await self.event_bus.publish(
                    EventType.MESSAGE_RETRIED,
                    {
                        "message_id": message_id,
                        "phone_number": phone_number,
                        "retry_count": retry_count + 1,
                        "status": result.get("status", MessageStatus.PENDING)
                    }
                )
                
                logger.info(f"Successfully retried message {message_id}")
                
        except Exception as e:
            logger.error(f"Error retrying message {message_id}: {e}")
            
            # Update status to failed with incremented retry count
            error_message = str(e)
            is_retryable = isinstance(e, RetryableError)
            
            await self.message_repository.update_message_status(
                message_id=message_id,
                status=MessageStatus.FAILED,
                event_type="retry_failed",
                reason=error_message,
                data={
                    "retry_count": retry_count + 1,
                    "retryable": is_retryable
                }
            )
            
            # Publish event
            await self.event_bus.publish(
                EventType.MESSAGE_RETRY_FAILED,
                {
                    "message_id": message_id,
                    "phone_number": phone_number,
                    "retry_count": retry_count + 1,
                    "error": error_message,
                    "retryable": is_retryable
                }
            )


# Singleton instance
_retry_engine = None

async def get_retry_engine():
    """Get the singleton retry engine instance."""
    global _retry_engine
    
    if _retry_engine is None:
        from app.db.session import get_repository
        from app.db.repositories.messages import MessageRepository
        from app.services.event_bus.bus import get_event_bus
        from app.services.sms.sender import get_sms_sender
        
        message_repository = await get_repository(MessageRepository)
        event_bus = get_event_bus()
        sms_sender = await get_sms_sender()
        
        _retry_engine = RetryEngine(
            message_repository=message_repository,
            event_bus=event_bus,
            sms_sender=sms_sender
        )
        
    return _retry_engine