"""
Event type definitions for the event bus.
"""
from enum import Enum, auto
from typing import Dict, Any, Optional
from datetime import datetime


class EventType(str, Enum):
    """Event types for the event bus."""
    
    # System events
    SYSTEM_STARTUP = "system:startup"
    SYSTEM_SHUTDOWN = "system:shutdown"
    
    # Message events
    MESSAGE_CREATED = "message:created"
    MESSAGE_UPDATED = "message:updated"
    MESSAGE_SENT = "message:sent"
    MESSAGE_DELIVERED = "message:delivered"
    MESSAGE_FAILED = "message:failed"
    MESSAGE_SCHEDULED = "message:scheduled"
    MESSAGE_RETRIED = "message:retried"
    MESSAGE_RETRY_FAILED = "message:retry_failed"
    
    # Batch events
    BATCH_CREATED = "batch:created"
    BATCH_UPDATED = "batch:updated"
    BATCH_COMPLETED = "batch:completed"

    # Campaign events
    CAMPAIGN_CREATED = "campaign:created"
    CAMPAIGN_UPDATED = "campaign:updated"
    CAMPAIGN_STARTED = "campaign:started"
    CAMPAIGN_PAUSED = "campaign:paused"
    CAMPAIGN_COMPLETED = "campaign:completed"
    CAMPAIGN_CANCELLED = "campaign:cancelled"
    CAMPAIGN_FAILED = "campaign:failed"
    
    # SMS Gateway events
    SMS_RECEIVED = "sms:received"
    SMS_SENT = "sms:sent"
    SMS_DELIVERED = "sms:delivered"
    SMS_FAILED = "sms:failed"
    
    # Webhook events
    WEBHOOK_RECEIVED = "webhook:received"
    WEBHOOK_PROCESSED = "webhook:processed"
    
    # User events
    USER_CREATED = "user:created"
    USER_UPDATED = "user:updated"
    USER_DELETED = "user:deleted"
    
    # API events
    API_REQUEST = "api:request"
    API_RESPONSE = "api:response"
    API_ERROR = "api:error"


class Event:
    """
    Base event class.
    
    Contains common event data and helper methods.
    """
    
    def __init__(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize event.
        
        Args:
            event_type: Event type
            data: Event data
            timestamp: Event timestamp
        """
        self.event_type = event_type
        self.data = data
        self.timestamp = timestamp or datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert event to dictionary.
        
        Returns:
            Dict: Event data
        """
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """
        Create event from dictionary.
        
        Args:
            data: Event data
            
        Returns:
            Event: Event instance
        """
        event_type = data.get("event_type")
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        
        return cls(
            event_type=event_type,
            data=data.get("data", {}),
            timestamp=timestamp
        )


class MessageEvent(Event):
    """
    Message event class.
    
    Contains message-specific event data.
    """
    
    def __init__(
        self,
        event_type: EventType,
        message_id: str,
        user_id: str,
        data: Dict[str, Any],
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize message event.
        
        Args:
            event_type: Event type
            message_id: Message ID
            user_id: User ID
            data: Event data
            timestamp: Event timestamp
        """
        # Add message ID and user ID to data
        data = data.copy()
        data["message_id"] = message_id
        data["user_id"] = user_id
        
        super().__init__(event_type, data, timestamp)
    
    @property
    def message_id(self) -> str:
        """Get message ID."""
        return self.data.get("message_id", "")
    
    @property
    def user_id(self) -> str:
        """Get user ID."""
        return self.data.get("user_id", "")


class WebhookEvent(Event):
    """
    Webhook event class.
    
    Contains webhook-specific event data.
    """
    
    def __init__(
        self,
        event_type: EventType,
        webhook_id: str,
        payload: Dict[str, Any],
        data: Dict[str, Any],
        timestamp: Optional[datetime] = None
    ):
        """
        Initialize webhook event.
        
        Args:
            event_type: Event type
            webhook_id: Webhook ID
            payload: Webhook payload
            data: Event data
            timestamp: Event timestamp
        """
        # Add webhook ID and payload to data
        data = data.copy()
        data["webhook_id"] = webhook_id
        data["payload"] = payload
        
        super().__init__(event_type, data, timestamp)
    
    @property
    def webhook_id(self) -> str:
        """Get webhook ID."""
        return self.data.get("webhook_id", "")
    
    @property
    def payload(self) -> Dict[str, Any]:
        """Get webhook payload."""
        return self.data.get("payload", {})