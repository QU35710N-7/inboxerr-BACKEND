# app/services/metrics/collector.py
import logging
from typing import Dict, Any, Optional

from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType

logger = logging.getLogger("inboxerr.metrics")

# Global metrics store (in-memory for MVP)
_metrics = {
    "messages": {
        "total": 0,
        "sent": 0,
        "delivered": 0,
        "failed": 0
    },
    "users": {
        "total": 0,
        "active": 0
    },
    "webhooks": {
        "total": 0,
        "delivered": 0,
        "failed": 0
    }
}

async def initialize_metrics() -> None:
    """Initialize metrics collector."""
    logger.info("Initializing metrics collector")
    
    # Subscribe to events
    event_bus = get_event_bus()
    
    # Message events
    await event_bus.subscribe(
        EventType.MESSAGE_CREATED,
        _handle_message_created,
        "metrics.message_created"
    )
    
    await event_bus.subscribe(
        EventType.MESSAGE_SENT,
        _handle_message_sent,
        "metrics.message_sent"
    )
    
    await event_bus.subscribe(
        EventType.MESSAGE_DELIVERED,
        _handle_message_delivered,
        "metrics.message_delivered"
    )
    
    await event_bus.subscribe(
        EventType.MESSAGE_FAILED,
        _handle_message_failed,
        "metrics.message_failed"
    )
    
    logger.info("Metrics collector initialized")

async def get_metrics() -> Dict[str, Any]:
    """Get current metrics."""
    return _metrics

async def _handle_message_created(data: Dict[str, Any]) -> None:
    """Handle message created event."""
    _metrics["messages"]["total"] += 1

async def _handle_message_sent(data: Dict[str, Any]) -> None:
    """Handle message sent event."""
    _metrics["messages"]["sent"] += 1

async def _handle_message_delivered(data: Dict[str, Any]) -> None:
    """Handle message delivered event."""
    _metrics["messages"]["delivered"] += 1

async def _handle_message_failed(data: Dict[str, Any]) -> None:
    """Handle message failed event."""
    _metrics["messages"]["failed"] += 1