# app/services/webhooks/manager.py
import logging
import hmac
import hashlib
import time
import json
from typing import Dict, Any, Optional, List, Tuple
import httpx
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone 

from app.core.config import settings
from app.core.exceptions import SMSGatewayError
from app.db.repositories.messages import MessageRepository
from app.db.repositories.webhooks import WebhookRepository
from app.schemas.message import MessageStatus
from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType
from app.services.webhooks.models import (
    WebhookPayload, SmsReceivedPayload, SmsSentPayload, 
    SmsDeliveredPayload, SmsFailedPayload, SystemPingPayload
)

logger = logging.getLogger("inboxerr.webhooks")

# Track registered webhooks
_registered_webhooks: Dict[str, str] = {}  # event_type -> webhook_id
_initialized = False

async def initialize_webhook_manager() -> None:
    """Initialize the webhook manager and register with SMS Gateway."""
    global _initialized
    
    if _initialized:
        return
        
    logger.info("Initializing webhook manager")
    
    # Register webhooks for each event type
    events_to_register = [
        "sms:sent", 
        "sms:delivered", 
        "sms:failed"
    ]
    
    for event_type in events_to_register:
        webhook_id = await register_webhook_with_gateway(event_type)
        if webhook_id:
            _registered_webhooks[event_type] = webhook_id
    
    _initialized = True
    logger.info(f"Webhook manager initialized, registered webhooks: {len(_registered_webhooks)}")

async def shutdown_webhook_manager() -> None:
    """Clean up webhook manager resources."""
    logger.info("Shutting down webhook manager")
    
    # Unregister all webhooks
    for event_type, webhook_id in _registered_webhooks.items():
        await unregister_webhook_from_gateway(webhook_id)
    
    _registered_webhooks.clear()
    logger.info("Webhook manager shutdown complete")

async def register_webhook_with_gateway(event_type: str) -> Optional[str]:
    """
    Register a webhook for a specific event type.
    
    Args:
        event_type: Event type to register for
        
    Returns:
        str: Webhook ID if registration successful
    """
    if not settings.SMS_GATEWAY_URL or not settings.SMS_GATEWAY_LOGIN or not settings.SMS_GATEWAY_PASSWORD:
        logger.warning("SMS Gateway credentials not configured, skipping webhook registration")
        return None
    
    # Webhook URL for the Gateway to call
    webhook_url = f"{settings.API_BASE_URL}{settings.API_PREFIX}/webhooks/gateway"
    
    try:
        # Create httpx client with authentication
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.SMS_GATEWAY_URL}/webhooks",
                auth=(settings.SMS_GATEWAY_LOGIN, settings.SMS_GATEWAY_PASSWORD),
                json={
                    "id": f"inboxerr-{event_type}",  # Custom ID for tracking
                    "url": webhook_url,
                    "event": event_type
                },
                timeout=10.0
            )
            
            if response.status_code in (200, 201):
                webhook_data = response.json()
                webhook_id = webhook_data.get("id")
                logger.info(f"Successfully registered webhook for {event_type}: {webhook_id}")
                return webhook_id
            else:
                logger.error(f"Failed to register webhook for {event_type}: {response.status_code} - {response.text}")
                return None
                
    except Exception as e:
        logger.error(f"Error registering webhook for {event_type}: {e}")
        return None

async def unregister_webhook_from_gateway(webhook_id: str) -> bool:
    """
    Unregister a webhook from SMS Gateway.
    
    Args:
        webhook_id: Webhook ID
        
    Returns:
        bool: True if unregistration successful
    """
    if not settings.SMS_GATEWAY_URL or not settings.SMS_GATEWAY_LOGIN or not settings.SMS_GATEWAY_PASSWORD:
        logger.warning("SMS Gateway credentials not configured, skipping webhook unregistration")
        return False
    
    try:
        # Create httpx client with authentication
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{settings.SMS_GATEWAY_URL}/webhooks/{webhook_id}",
                auth=(settings.SMS_GATEWAY_LOGIN, settings.SMS_GATEWAY_PASSWORD),
                timeout=10.0
            )
            
            if response.status_code in (200, 204):
                logger.info(f"Successfully unregistered webhook: {webhook_id}")
                return True
            else:
                logger.error(f"Failed to unregister webhook: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"Error unregistering webhook: {e}")
        return False

async def process_gateway_webhook(raw_body: bytes, headers: Dict[str, str]) -> Tuple[bool, Dict[str, Any]]:
    """
    Process a webhook received from the SMS Gateway with enhanced error handling.
    
    Args:
        raw_body: Raw request body
        headers: Request headers
        
    Returns:
        Tuple[bool, Dict]: (success, processed_data)
    """
    # Decode raw body for payload processing
    try:
        payload_str = raw_body.decode('utf-8')
    except UnicodeDecodeError:
        logger.error("Failed to decode webhook payload")
        return False, {"error": "Invalid payload encoding"}
    
    try:
        # Parse JSON
        try:
            payload_dict = json.loads(payload_str)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in webhook: {e}")
            return False, {"error": "Invalid JSON payload", "details": str(e)}
        
        # Verify webhook signature if enabled
        if settings.WEBHOOK_SIGNATURE_KEY:
            signature_valid, signature_error = verify_webhook_signature(payload_str, headers)
            if not signature_valid:
                logger.warning(f"Invalid webhook signature: {signature_error}")
                return False, {"error": "Invalid signature", "details": signature_error}
        
        # Validate basic payload structure
        try:
            base_payload = WebhookPayload(**payload_dict)
        except Exception as e:
            logger.error(f"Invalid webhook payload structure: {e}")
            return False, {"error": "Invalid payload structure", "details": str(e)}
        
        event_type = base_payload.event
        gateway_id = base_payload.id
        
        logger.info(f"Processing webhook event: {event_type}, gateway ID: {gateway_id}")
        
        # Record the incoming webhook event in database
        try:
            from app.db.session import get_repository
            webhook_repo = await get_repository(WebhookRepository)
            
            # Create webhook event record
            await webhook_repo.create_webhook_event(
                event_type=event_type,
                payload=payload_dict,
                phone_number=payload_dict.get("payload", {}).get("phoneNumber"),
                gateway_message_id=gateway_id
            )
        except Exception as e:
            # Log error but continue processing - this is just for auditing
            logger.error(f"Error recording webhook event: {e}")
        
        # Process based on event type
        try:
            if event_type == "sms:received":
                payload = SmsReceivedPayload(**payload_dict["payload"])
                result = await process_sms_received(base_payload, payload)
            elif event_type == "sms:sent":
                payload = SmsSentPayload(**payload_dict["payload"])
                result = await process_sms_sent(base_payload, payload)
            elif event_type == "sms:delivered":
                payload = SmsDeliveredPayload(**payload_dict["payload"])
                result = await process_sms_delivered(base_payload, payload)
            elif event_type == "sms:failed":
                payload = SmsFailedPayload(**payload_dict["payload"])
                result = await process_sms_failed(base_payload, payload)
            elif event_type == "system:ping":
                payload = SystemPingPayload(**payload_dict["payload"])
                result = await process_system_ping(base_payload, payload)
            else:
                logger.warning(f"Unknown webhook event type: {event_type}")
                return False, {"error": "Unknown event type", "event_type": event_type}
                
            # Log successful processing
            logger.info(f"Successfully processed webhook event: {event_type}, gateway ID: {gateway_id}")
            
            # Mark event as processed if we created one
            try:
                webhook_events = await webhook_repo.get_unprocessed_events(limit=10)
                for event in webhook_events:
                    if event.gateway_message_id == gateway_id:
                        await webhook_repo.mark_event_processed(event_id=event.id)
            except Exception as e:
                logger.error(f"Error marking webhook event as processed: {e}")
            
            return True, result
            
        except Exception as e:
            logger.error(f"Error processing webhook event {event_type}: {e}", exc_info=True)
            
            # Try to handle specific event processing errors gracefully
            error_details = {"error_type": type(e).__name__, "gateway_id": gateway_id}
            
            # Publish error event
            try:
                from app.services.event_bus.bus import get_event_bus
                from app.services.event_bus.events import EventType
                
                event_bus = get_event_bus()
                await event_bus.publish(
                    EventType.WEBHOOK_PROCESSED,
                    {
                        "success": False,
                        "event_type": event_type,
                        "gateway_id": gateway_id,
                        "error": str(e),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                )
            except Exception as publish_error:
                logger.error(f"Error publishing webhook error event: {publish_error}")
                
            return False, {"error": f"Error processing {event_type} event", "details": str(e), **error_details}
            
    except Exception as e:
        logger.error(f"Unexpected error in webhook processing: {e}", exc_info=True)
        return False, {"error": "Unexpected error", "details": str(e)}


def verify_webhook_signature(payload: str, headers: Dict[str, str]) -> Tuple[bool, Optional[str]]:
    """
    Verify webhook signature from SMS Gateway with detailed error reporting.
    
    Args:
        payload: Webhook payload string
        headers: Request headers
        
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message_or_none)
    """
    signature = headers.get("X-Signature")
    timestamp = headers.get("X-Timestamp")
    
    if not signature:
        return False, "Missing X-Signature header"
    
    if not timestamp:
        return False, "Missing X-Timestamp header"
    
    # Verify timestamp is recent (within tolerance)
    try:
        ts = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - ts) > settings.WEBHOOK_TIMESTAMP_TOLERANCE:
            return False, f"Timestamp too old: {timestamp} (current: {current_time})"
    except (ValueError, TypeError):
        return False, f"Invalid timestamp format: {timestamp}"
    
    # Calculate expected signature
    message = (payload + timestamp).encode()
    expected_signature = hmac.new(
        settings.WEBHOOK_SIGNATURE_KEY.encode(),
        message,
        hashlib.sha256
    ).hexdigest()
    
    # Compare signatures (constant-time comparison)
    is_valid = hmac.compare_digest(expected_signature, signature)
    
    return is_valid, None if is_valid else "Signature mismatch"

def verify_webhook_signature(payload: str, headers: Dict[str, str]) -> bool:
    """
    Verify webhook signature from SMS Gateway.
    
    Args:
        payload: Webhook payload string
        headers: Request headers
        
    Returns:
        bool: True if signature is valid
    """
    signature = headers.get("X-Signature")
    timestamp = headers.get("X-Timestamp")
    
    if not signature or not timestamp:
        logger.warning("Missing signature headers")
        return False
    
    # Verify timestamp is recent (within tolerance)
    try:
        ts = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - ts) > settings.WEBHOOK_TIMESTAMP_TOLERANCE:
            logger.warning(f"Webhook timestamp too old: {timestamp}")
            return False
    except (ValueError, TypeError):
        logger.warning(f"Invalid timestamp: {timestamp}")
        return False
    
    # Calculate expected signature
    message = (payload + timestamp).encode()
    expected_signature = hmac.new(
        settings.WEBHOOK_SIGNATURE_KEY.encode(),
        message,
        hashlib.sha256
    ).hexdigest()
    
    # Compare signatures (constant-time comparison)
    return hmac.compare_digest(expected_signature, signature)

async def process_sms_received(base_payload: WebhookPayload, payload: SmsReceivedPayload) -> Dict[str, Any]:
    """Process SMS received event."""
    # For inbound messages - not the main focus for now
    logger.info(f"Received SMS: {payload.phone_number} -> '{payload.message}'")
    
    # Publish event for other components
    event_bus = get_event_bus()
    await event_bus.publish(
        EventType.SMS_RECEIVED,
        {
            "gateway_id": base_payload.id,
            "device_id": base_payload.device_id,
            "phone_number": payload.phone_number,
            "message": payload.message,
            "timestamp": payload.received_at.isoformat()
        }
    )
    
    return {
        "status": "processed",
        "event": "sms:received",
        "phone_number": payload.phone_number
    }

async def process_sms_sent(base_payload: WebhookPayload, payload: SmsSentPayload) -> Dict[str, Any]:
    """Process SMS sent event."""
    logger.info(f"SMS sent to {payload.phone_number}, gateway ID: {base_payload.id}")
    
    # Get message repository
    from app.db.session import get_repository
    message_repo = await get_repository(MessageRepository)
    
    # Extract gateway message ID
    gateway_id = base_payload.id
    
    # Find message by gateway ID
    message = await message_repo.get_by_gateway_id(gateway_id)
    if not message:
        # This could be normal if we didn't originate this message
        logger.info(f"No matching message found for gateway ID: {gateway_id}")
        return {
            "status": "acknowledged",
            "event": "sms:sent",
            "message_found": False
        }
    
    # Update message status
    updated_message = await message_repo.update_message_status(
        message_id=message.id,
        status=MessageStatus.SENT,
        event_type="webhook",
        gateway_message_id=gateway_id,
        data=jsonable_encoder(base_payload)
    )
    
    if not updated_message:
        logger.warning(f"Failed to update message status for ID: {message.id}")
        return {
            "status": "error",
            "event": "sms:sent",
            "message_id": message.id,
            "error": "Failed to update message status"
        }
    
    # Publish event
    event_bus = get_event_bus()
    await event_bus.publish(
        EventType.MESSAGE_SENT,
        {
            "message_id": message.id,
            "gateway_id": gateway_id,
            "phone_number": payload.phone_number,
            "user_id": message.user_id,
            "timestamp": payload.sent_at.isoformat()
        }
    )
    
    return {
        "status": "processed",
        "event": "sms:sent",
        "message_id": message.id,
        "phone_number": payload.phone_number
    }

async def process_sms_delivered(base_payload: WebhookPayload, payload: SmsDeliveredPayload) -> Dict[str, Any]:
    """Process SMS delivered event."""
    logger.info(f"SMS delivered to {payload.phone_number}, gateway ID: {base_payload.id}")
    
    # Get message repository
    from app.db.session import get_repository
    message_repo = await get_repository(MessageRepository)
    
    # Extract gateway message ID
    gateway_id = base_payload.id
    
    # Find message by gateway ID
    message = await message_repo.get_by_gateway_id(gateway_id)
    if not message:
        logger.info(f"No matching message found for gateway ID: {gateway_id}")
        return {
            "status": "acknowledged",
            "event": "sms:delivered",
            "message_found": False
        }
    
    # Update message status
    updated_message = await message_repo.update_message_status(
        message_id=message.id,
        status=MessageStatus.DELIVERED,
        event_type="webhook",
        gateway_message_id=gateway_id,
        data=jsonable_encoder(base_payload)
    )
    
    if not updated_message:
        logger.warning(f"Failed to update message status for ID: {message.id}")
        return {
            "status": "error",
            "event": "sms:delivered",
            "message_id": message.id,
            "error": "Failed to update message status"
        }
    
    # Publish event
    event_bus = get_event_bus()
    await event_bus.publish(
        EventType.MESSAGE_DELIVERED,
        {
            "message_id": message.id,
            "gateway_id": gateway_id,
            "phone_number": payload.phone_number,
            "user_id": message.user_id,
            "timestamp": payload.delivered_at.isoformat()
        }
    )
    
    return {
        "status": "processed",
        "event": "sms:delivered",
        "message_id": message.id,
        "phone_number": payload.phone_number
    }

async def process_sms_failed(base_payload: WebhookPayload, payload: SmsFailedPayload) -> Dict[str, Any]:
    """Process SMS failed event."""
    logger.info(f"SMS failed for {payload.phone_number}, reason: {payload.reason}, gateway ID: {base_payload.id}")
    
    # Get message repository
    from app.db.session import get_repository
    message_repo = await get_repository(MessageRepository)
    
    # Extract gateway message ID and failure reason
    gateway_id = base_payload.id
    reason = payload.reason
    
    # Find message by gateway ID
    message = await message_repo.get_by_gateway_id(gateway_id)
    if not message:
        logger.info(f"No matching message found for gateway ID: {gateway_id}")
        return {
            "status": "acknowledged",
            "event": "sms:failed",
            "message_found": False
        }
    
    # Update message status
    updated_message = await message_repo.update_message_status(
        message_id=message.id,
        status=MessageStatus.FAILED,
        event_type="webhook",
        reason=reason,
        gateway_message_id=gateway_id,
        data=jsonable_encoder(base_payload)
    )
    
    if not updated_message:
        logger.warning(f"Failed to update message status for ID: {message.id}")
        return {
            "status": "error",
            "event": "sms:failed",
            "message_id": message.id,
            "error": "Failed to update message status"
        }
    
    # Publish event
    event_bus = get_event_bus()
    await event_bus.publish(
        EventType.MESSAGE_FAILED,
        {
            "message_id": message.id,
            "gateway_id": gateway_id,
            "phone_number": payload.phone_number,
            "user_id": message.user_id,
            "reason": reason,
            "timestamp": payload.failed_at.isoformat()
        }
    )
    
    return {
        "status": "processed",
        "event": "sms:failed",
        "message_id": message.id,
        "phone_number": payload.phone_number,
        "reason": reason
    }

async def process_system_ping(base_payload: WebhookPayload, payload: SystemPingPayload) -> Dict[str, Any]:
    """Process system ping event."""
    logger.info(f"System ping received from device: {base_payload.device_id}")
    
    # Simple acknowledgment
    return {
        "status": "acknowledged",
        "event": "system:ping",
        "device_id": base_payload.device_id
    }

async def fetch_registered_webhooks_from_gateway() -> List[Dict[str, Any]]:
    """
    Fetch registered webhooks from the SMS Gateway.
    Returns a list of registered webhooks or raises an error.
    """
    if not settings.SMS_GATEWAY_URL or not settings.SMS_GATEWAY_LOGIN or not settings.SMS_GATEWAY_PASSWORD:
        raise SMSGatewayError("SMS Gateway credentials not configured", code="SMS_GATEWAY_CONFIG_MISSING")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.SMS_GATEWAY_URL}/webhooks",
                auth=(settings.SMS_GATEWAY_LOGIN, settings.SMS_GATEWAY_PASSWORD),
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error fetching registered webhooks: {e}")
        raise
