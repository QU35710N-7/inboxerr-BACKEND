# app/services/webhooks/models.py
"""
Pydantic models for webhook payloads from SMS Gateway.
"""
from datetime import datetime
from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

class SmsReceivedPayload(BaseModel):
    """Payload for sms:received event."""
    message_id: str = Field(..., alias="messageId")
    message: str
    phone_number: str = Field(..., alias="phoneNumber")
    sim_number: Optional[int] = Field(None, alias="simNumber")
    received_at: datetime = Field(..., alias="receivedAt")
    
class SmsSentPayload(BaseModel):
    """Payload for sms:sent event."""
    message_id: str = Field(..., alias="messageId")
    phone_number: str = Field(..., alias="phoneNumber")
    sim_number: Optional[int] = Field(None, alias="simNumber")
    sent_at: datetime = Field(..., alias="sentAt")
    
class SmsDeliveredPayload(BaseModel):
    """Payload for sms:delivered event."""
    message_id: str = Field(..., alias="messageId")
    phone_number: str = Field(..., alias="phoneNumber")
    sim_number: Optional[int] = Field(None, alias="simNumber")
    delivered_at: datetime = Field(..., alias="deliveredAt")
    
class SmsFailedPayload(BaseModel):
    """Payload for sms:failed event."""
    message_id: str = Field(..., alias="messageId")
    phone_number: str = Field(..., alias="phoneNumber")
    sim_number: Optional[int] = Field(None, alias="simNumber")
    failed_at: datetime = Field(..., alias="failedAt")
    reason: str
    
class SystemPingPayload(BaseModel):
    """Payload for system:ping event."""
    health: Dict[str, Any]
    
EventType = Literal["sms:received", "sms:sent", "sms:delivered", "sms:failed", "system:ping"]

class WebhookPayload(BaseModel):
    """Base webhook payload from SMS Gateway."""
    device_id: str = Field(..., alias="deviceId")
    event: EventType
    id: str
    webhook_id: str = Field(..., alias="webhookId")
    payload: Dict[str, Any]  # Will be converted to specific payload types based on event