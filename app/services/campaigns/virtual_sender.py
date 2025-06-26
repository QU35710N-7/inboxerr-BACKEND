# app/services/campaigns/virtual_sender.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import uuid
from enum import Enum

from app.core.config import settings
from app.db.repositories.campaigns import CampaignRepository
from app.db.repositories.contacts import ContactRepository
from app.db.repositories.templates import TemplateRepository
from app.db.repositories.messages import MessageRepository
from app.schemas.message import MessageStatus
from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType
from app.services.sms.sender import SMSSender, get_sms_sender
from app.db.session import get_repository_context

logger = logging.getLogger("inboxerr.virtual_sender")

class CircuitBreakerState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """Simple circuit breaker for SMS gateway protection."""
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitBreakerState.CLOSED
        
    async def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        if self.state == CircuitBreakerState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitBreakerState.HALF_OPEN
                logger.info("Circuit breaker: Testing recovery (HALF_OPEN)")
            else:
                raise Exception("Circuit breaker OPEN - SMS gateway unavailable")
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e
    
    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return True
        return datetime.now() - self.last_failure_time >= timedelta(seconds=self.timeout)
    
    def _on_success(self):
        self.failure_count = 0
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.CLOSED
            logger.info("Circuit breaker: Reset to CLOSED")
    
    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            logger.error(f"Circuit breaker OPENED after {self.failure_count} failures")

class VirtualMessage:
    """Virtual message object for on-demand generation."""
    
    def __init__(
        self,
        phone_number: str,
        message_text: str,
        contact_name: Optional[str] = None,
        contact_data: Optional[Dict[str, Any]] = None,
        campaign_id: Optional[str] = None,
        user_id: Optional[str] = None
    ):
        self.phone_number = phone_number
        self.message_text = message_text
        self.contact_name = contact_name
        self.contact_data = contact_data or {}
        self.campaign_id = campaign_id
        self.user_id = user_id
        self.custom_id = str(uuid.uuid4())

class VirtualCampaignSender:
    """
    Focused virtual campaign sender - generates and sends messages.
    
    Responsibilities:
    - Generate messages from templates and contacts
    - Send via SMS gateway with rate limiting
    - Circuit breaker protection
    - Create tracking records
    
    NOT responsible for:
    - Retries (handled by dedicated retry engine)
    - Complex error recovery
    - Queue management
    """
    
    def __init__(self, sms_sender: SMSSender, event_bus: Any):
        """Initialize with SMS sender and event bus."""
        self.sms_sender = sms_sender
        self.event_bus = event_bus
        
        # Production settings
        self._chunk_size = settings.BATCH_SIZE or 100
        self._micro_batch_size = getattr(settings, 'VIRTUAL_SENDER_MICRO_BATCH_SIZE', 10)
        self._max_concurrent = getattr(settings, 'VIRTUAL_SENDER_MAX_CONCURRENT', 2)
        self._rate_limit_delay = getattr(settings, 'VIRTUAL_SENDER_RATE_LIMIT_DELAY', 0.2)
        
        # Concurrency controls
        self._semaphore = asyncio.Semaphore(5)
        self._send_semaphore = asyncio.Semaphore(self._max_concurrent)
        
        # Circuit breaker for gateway protection
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=getattr(settings, 'VIRTUAL_SENDER_CIRCUIT_BREAKER_THRESHOLD', 5),
            timeout=getattr(settings, 'VIRTUAL_SENDER_CIRCUIT_BREAKER_TIMEOUT', 60)
        )
        
        logger.info(f"VirtualCampaignSender: concurrent={self._max_concurrent}, micro_batch={self._micro_batch_size}")
    
    async def process_virtual_campaign(self, campaign_id: str) -> bool:
        """Process a virtual campaign by generating messages on-demand."""
        try:
            # Get campaign details
            async with get_repository_context(CampaignRepository) as campaign_repo:
                campaign = await campaign_repo.get_by_id(campaign_id)
                if not campaign or campaign.status != "active":
                    return False
                
                is_virtual = campaign.settings.get("virtual_messaging", False)
                if not is_virtual:
                    logger.warning(f"Campaign {campaign_id} is not virtual")
                    return False
                
                import_job_id = campaign.settings.get("import_job_id")
                if not import_job_id:
                    logger.error(f"Campaign {campaign_id} missing import_job_id")
                    return False
            
            # Process with semaphore
            async with self._semaphore:
                await self._process_virtual_campaign_chunks(
                    campaign_id, import_job_id, campaign.template_id, campaign.user_id
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing virtual campaign {campaign_id}: {e}", exc_info=True)
            # Mark as failed - retry engine will handle recovery
            try:
                async with get_repository_context(CampaignRepository) as campaign_repo:
                    await campaign_repo.update_campaign_status(
                        campaign_id=campaign_id,
                        status="failed",
                        completed_at=datetime.now(timezone.utc)
                    )
            except Exception as update_error:
                logger.error(f"Failed to update campaign status: {update_error}")
            return False
    
    async def _process_virtual_campaign_chunks(
        self, 
        campaign_id: str, 
        import_job_id: str, 
        template_id: str,
        user_id: str
    ) -> None:
        """Process virtual campaign in chunks."""
        
        # Get template content
        template_content = None
        async with get_repository_context(TemplateRepository) as template_repo:
            template = await template_repo.get_by_id(template_id)
            if not template:
                raise ValueError(f"Template {template_id} not found")
            template_content = template.content
        
        offset = 0
        
        while True:
            # Check if campaign is still active
            async with get_repository_context(CampaignRepository) as campaign_repo:
                campaign = await campaign_repo.get_by_id(campaign_id)
                if not campaign or campaign.status != "active":
                    logger.info(f"Campaign {campaign_id} no longer active, stopping")
                    return
            
            # Get next chunk of contacts
            contacts = []
            async with get_repository_context(ContactRepository) as contact_repo:
                contacts, _ = await contact_repo.get_by_import_id(
                    import_job_id, skip=offset, limit=self._chunk_size
                )
            
            if not contacts:
                # Mark campaign as completed
                async with get_repository_context(MessageRepository) as msg_repo:
                    real_total = await msg_repo.count_messages_for_campaign(campaign_id)

                async with get_repository_context(CampaignRepository) as campaign_repo:
                    campaign = await campaign_repo.get_by_id(campaign_id)
                    if campaign and campaign.status != "completed":
                        await campaign_repo.update(
                            id=campaign_id,
                            obj_in={
                                "status": "completed",
                                "completed_at": datetime.now(timezone.utc),
                                "total_messages": real_total
                            }
                        )
                        logger.info(f"Campaign {campaign_id} completed: {real_total} total messages")
                return
            
            # Process chunk with micro-batching
            chunk_sent, chunk_failed = await self._process_contact_chunk(
                contacts, template_content, campaign_id, user_id
            )
            
            # Update campaign statistics
            async with get_repository_context(CampaignRepository) as campaign_repo:
                await campaign_repo.update_campaign_stats(
                    campaign_id=campaign_id,
                    increment_sent=chunk_sent,
                    increment_failed=chunk_failed
                )
            
            offset += len(contacts)
            await asyncio.sleep(0.5)  # Breathing room between chunks
    
    async def _process_contact_chunk(
        self, 
        contacts: List[Any], 
        template_content: str,
        campaign_id: str,
        user_id: str
    ) -> tuple[int, int]:
        """Process contacts in memory-efficient micro-batches."""
        
        total_sent = 0
        total_failed = 0
        
        # Process in micro-batches to limit memory and DB connections
        for i in range(0, len(contacts), self._micro_batch_size):
            micro_batch = contacts[i:i + self._micro_batch_size]
            
            # Create parallel tasks for micro-batch
            tasks = []
            for contact in micro_batch:
                task = asyncio.create_task(
                    self._process_single_contact(contact, template_content, campaign_id, user_id)
                )
                tasks.append(task)
            
            # Wait for all tasks with error isolation
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count results
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Contact {micro_batch[i].phone} failed: {result}")
                    total_failed += 1
                elif result == "sent":
                    total_sent += 1
                elif result == "failed":
                    total_failed += 1
                # "skipped" doesn't count as failure
            
            # Small delay between micro-batches
            await asyncio.sleep(0.1)
        
        return total_sent, total_failed
    
    async def _process_single_contact(
        self,
        contact: Any,
        template_content: str,
        campaign_id: str,
        user_id: str
    ) -> str:
        """Process single contact - send once, let retry engine handle failures."""
        
        async with self._send_semaphore:  # Rate limiting
            try:
                # Check for duplicates
                already_sent = await self._check_already_sent(campaign_id, contact.phone)
                if already_sent:
                    return "skipped"

                # Generate message
                virtual_message = self._generate_virtual_message(
                    contact, template_content, campaign_id, user_id
                )
                
                # Send with circuit breaker protection
                result = await self._circuit_breaker.call(
                    self.sms_sender._send_to_gateway,
                    phone_number=virtual_message.phone_number,
                    message_text=virtual_message.message_text,
                    custom_id=virtual_message.custom_id
                )
                
                # Create success record
                await self._create_message_record(virtual_message, result, MessageStatus.SENT)
                
                # Rate limiting
                await asyncio.sleep(self._rate_limit_delay)
                
                return "sent"
                
            except Exception as e:
                # Create failed record - retry engine will handle retry
                try:
                    virtual_message = self._generate_virtual_message(
                        contact, template_content, campaign_id, user_id
                    )
                    await self._create_message_record(
                        virtual_message, {"error": str(e)}, MessageStatus.FAILED
                    )
                except Exception as record_error:
                    logger.error(f"Failed to create failed record: {record_error}")
                
                return "failed"
    
    def _generate_virtual_message(
        self, 
        contact: Any, 
        template_content: str,
        campaign_id: str,
        user_id: str
    ) -> VirtualMessage:
        """Generate virtual message from contact and template."""
        
        personalized_message = template_content
        
        # Simple template substitution
        if contact.name:
            personalized_message = personalized_message.replace("{{name}}", contact.name)
            personalized_message = personalized_message.replace("{{contact_name}}", contact.name)
        
        personalized_message = personalized_message.replace("{{phone}}", contact.phone)
        
        # Additional custom fields if available
        if hasattr(contact, 'custom_fields') and contact.custom_fields:
            for key, value in contact.custom_fields.items():
                personalized_message = personalized_message.replace(f"{{{{{key}}}}}", str(value))
        
        return VirtualMessage(
            phone_number=contact.phone,
            message_text=personalized_message,
            contact_name=contact.name,
            contact_data={"name": contact.name, "tags": contact.tags or []},
            campaign_id=campaign_id,
            user_id=user_id
        )
    
    async def _check_already_sent(self, campaign_id: str, phone_number: str) -> bool:
        """Check for duplicate sends."""
        try:
            async with get_repository_context(MessageRepository) as message_repo:
                existing_message = await message_repo.get_message_by_campaign_and_phone(
                    campaign_id=campaign_id, phone_number=phone_number
                )
                return existing_message is not None
                
        except Exception as e:
            logger.error(f"Error checking duplicate: {e}")
            return True  # Fail-safe: assume already sent
    
    async def _create_message_record(
        self, 
        virtual_message: VirtualMessage, 
        send_result: Dict[str, Any],
        status: MessageStatus
    ) -> None:
        """Create message record for tracking."""
        
        try:
            async with get_repository_context(MessageRepository) as message_repo:
                message = await message_repo.create_message(
                    phone_number=virtual_message.phone_number,
                    message_text=virtual_message.message_text,
                    user_id=virtual_message.user_id,
                    campaign_id=virtual_message.campaign_id,
                    custom_id=virtual_message.custom_id,
                    metadata={
                        "contact_name": virtual_message.contact_name,
                        "contact_data": virtual_message.contact_data,
                        "virtual_generated": True,
                        "send_result": send_result
                    }
                )

                await message_repo.update_message_status(
                    message_id=message.id,
                    status=status,
                    event_type="virtual_send",
                    gateway_message_id=send_result.get("gateway_message_id"),
                    data=send_result,
                )

        except Exception as e:
            logger.error(f"Failed to create message record: {e}")

# Dependency injection
async def get_virtual_campaign_sender() -> VirtualCampaignSender:
    """Get virtual campaign sender service instance."""
    sms_sender = await get_sms_sender()
    event_bus = get_event_bus()
    return VirtualCampaignSender(sms_sender, event_bus)