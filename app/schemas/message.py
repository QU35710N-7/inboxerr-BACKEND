"""
Pydantic schemas for message-related API operations.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field, validator


class MessageStatus(str, Enum):
    """Possible message statuses."""
    PENDING = "pending"
    PROCESSED = "processed"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class MessageCreate(BaseModel):
    """Schema for creating a new message."""
    phone_number: str = Field(..., description="Recipient phone number in E.164 format")
    message: str = Field(..., description="Message content")
    scheduled_at: Optional[datetime] = Field(None, description="Schedule message for future delivery")
    custom_id: Optional[str] = Field(None, description="Custom ID for tracking")
    
    @validator("phone_number")
    def validate_phone_number(cls, v):
        """Validate phone number format."""
        # Basic validation - will be handled more thoroughly in the service
        if not v or not (v.startswith("+") and len(v) >= 8):
            raise ValueError("Phone number must be in E.164 format (e.g. +1234567890)")
        return v
    
    @validator("message")
    def validate_message(cls, v):
        """Validate message content."""
        if not v or len(v.strip()) == 0:
            raise ValueError("Message cannot be empty")
        if len(v) > 1600:  # Allow for multi-part SMS
            raise ValueError("Message exceeds maximum length of 1600 characters")
        return v


class MessageResponse(BaseModel):
    """Schema for message response."""
    id: str = Field(..., description="Message ID")
    custom_id: Optional[str] = Field(None, description="Custom ID if provided")
    phone_number: str = Field(..., description="Recipient phone number")
    message: str = Field(..., description="Message content")
    status: MessageStatus = Field(..., description="Current message status")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    scheduled_at: Optional[datetime] = Field(None, description="Scheduled delivery time")
    sent_at: Optional[datetime] = Field(None, description="Time when message was sent")
    delivered_at: Optional[datetime] = Field(None, description="Time when message was delivered")
    failed_at: Optional[datetime] = Field(None, description="Time when message failed")
    reason: Optional[str] = Field(None, description="Failure reason if applicable")
    gateway_message_id: Optional[str] = Field(None, description="ID from SMS gateway")
    user_id: str = Field(..., description="User who sent the message")
    meta_data: Optional[Dict[str, Any]] = Field(default={}, description="Additional metadata")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class MessageStatusUpdate(BaseModel):
    """Schema for updating message status."""
    status: MessageStatus = Field(..., description="New message status")
    reason: Optional[str] = Field(None, description="Reason for status change (required for FAILED)")
    
    @validator("reason")
    def validate_reason(cls, v, values):
        """Validate reason field."""
        if values.get("status") == MessageStatus.FAILED and not v:
            raise ValueError("Reason is required when status is FAILED")
        return v


class BatchOptions(BaseModel):
    """Options for batch processing."""
    delay_between_messages: Optional[float] = Field(0.3, description="Delay between messages in seconds")
    fail_on_first_error: Optional[bool] = Field(False, description="Stop processing on first error")
    retry_failed: Optional[bool] = Field(True, description="Automatically retry failed messages")


class BatchMessageRequest(BaseModel):
    """Schema for batch message request."""
    messages: List[MessageCreate] = Field(..., description="List of messages to send")
    options: Optional[BatchOptions] = Field(default=None, description="Batch processing options")
    
    @validator("messages")
    def validate_messages(cls, v):
        """Validate message list."""
        if not v:
            raise ValueError("Message list cannot be empty")
        if len(v) > 1000:
            raise ValueError("Maximum batch size is 1000 messages")
        return v


class BatchMessageResponse(BaseModel):
    """Schema for batch message response."""
    batch_id: str = Field(..., description="Batch ID for tracking")
    total: int = Field(..., description="Total number of messages in batch")
    processed: int = Field(..., description="Number of messages processed")
    successful: int = Field(..., description="Number of successful messages")
    failed: int = Field(..., description="Number of failed messages")
    status: str = Field(..., description="Overall batch status")
    created_at: datetime = Field(..., description="Batch creation timestamp")
    messages: Optional[List[MessageResponse]] = Field(None, description="List of message responses")