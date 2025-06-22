"""
Pydantic schemas for message-related API operations.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field, validator
from app.schemas.campaign import CampaignResponse


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
    variables: Optional[Dict[str, Any]] = Field(None, description="Variables for message personalization")

    
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
    
    # Personalization variables  
    variables: Optional[Dict[str, Any]] = Field(None, description="Variables used for message personalization")
    
    campaign: Optional[CampaignResponse] = Field(None, description="Campaign information if message belongs to a campaign")
    parts_count: Optional[int] = Field(None, description="Number of SMS parts")


    
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

class CampaignBulkDeleteRequest(BaseModel):
    """Schema for campaign-scoped bulk delete request."""
    status: Optional[MessageStatus] = Field(None, description="Filter by message status (e.g., 'failed', 'sent')")
    from_date: Optional[datetime] = Field(None, description="Delete messages from this date onwards (ISO format)")
    to_date: Optional[datetime] = Field(None, description="Delete messages up to this date (ISO format)")
    limit: int = Field(default=1000, le=10000, description="Maximum number of messages to delete (max 10,000)")
    confirm_delete: bool = Field(default=True, description="Confirmation flag - must be true to proceed")
    force_delete: bool = Field(default=False, description="Force delete messages with delivery events")
    confirmation_token: Optional[str] = Field(None, description="Required when force_delete=True - must be 'CONFIRM'")
    batch_size: int = Field(default=1000, le=5000, description="Process deletions in batches for server stability")
    
    @validator("limit")
    def validate_limit(cls, v):
        """Validate deletion limit for safety."""
        if v <= 0:
            raise ValueError("Limit must be greater than 0")
        if v > 10000:
            raise ValueError("Maximum limit is 10,000 messages per operation")
        return v
    
    @validator("batch_size")
    def validate_batch_size(cls, v):
        """Validate batch size for server stability."""
        if v <= 0:
            raise ValueError("Batch size must be greater than 0")
        if v > 5000:
            raise ValueError("Maximum batch size is 5,000 for server stability")
        return v
    
    @validator("confirmation_token")
    def validate_confirmation_token(cls, v, values):
        """Validate confirmation token when force delete is enabled."""
        if values.get("force_delete") and v != "CONFIRM":
            raise ValueError("confirmation_token must be 'CONFIRM' when force_delete is true")
        return v
    
    @validator("confirm_delete")
    def validate_confirmation(cls, v):
        """Ensure user confirms the bulk deletion."""
        if not v:
            raise ValueError("confirm_delete must be true to proceed with bulk deletion")
        return v
    
    @validator("from_date", "to_date")
    def validate_dates(cls, v):
        """Validate date format and timezone."""
        if v is not None:
            # Ensure datetime is timezone-aware
            if v.tzinfo is None:
                raise ValueError("Date must be timezone-aware (include timezone information)")
        return v

class GlobalBulkDeleteRequest(BaseModel):
    """Schema for global bulk delete request (by message IDs)."""
    message_ids: List[str] = Field(..., description="List of message IDs to delete")
    campaign_id: Optional[str] = Field(None, description="Optional campaign context for validation")
    confirm_delete: bool = Field(default=True, description="Confirmation flag - must be true to proceed")
    force_delete: bool = Field(default=False, description="Force delete messages with delivery events")
    confirmation_token: Optional[str] = Field(None, description="Required when force_delete=True - must be 'CONFIRM'")
    
    @validator("message_ids")
    def validate_message_ids(cls, v):
        """Validate message ID list."""
        if not v:
            raise ValueError("Message IDs list cannot be empty")
        if len(v) > 1000:
            raise ValueError("Maximum 1,000 message IDs per global bulk operation")
        
        # Check for duplicates
        if len(v) != len(set(v)):
            raise ValueError("Duplicate message IDs found in request")
        
        return v
    
    @validator("confirmation_token")
    def validate_confirmation_token(cls, v, values):
        """Validate confirmation token when force delete is enabled."""
        if values.get("force_delete") and v != "CONFIRM":
            raise ValueError("confirmation_token must be 'CONFIRM' when force_delete is true")
        return v
    
    @validator("confirm_delete")
    def validate_confirmation(cls, v):
        """Ensure user confirms the bulk deletion."""
        if not v:
            raise ValueError("confirm_delete must be true to proceed with bulk deletion")
        return v


class BulkDeleteResponse(BaseModel):
    """Schema for bulk delete operation response."""
    deleted_count: int = Field(..., description="Number of messages successfully deleted")
    campaign_id: Optional[str] = Field(None, description="Campaign ID if campaign-scoped operation")
    failed_count: int = Field(default=0, description="Number of messages that failed to delete")
    errors: List[str] = Field(default=[], description="List of error messages if any failures occurred")
    operation_type: str = Field(..., description="Type of bulk operation ('campaign' or 'global')")
    filters_applied: Dict[str, Any] = Field(default={}, description="Filters that were applied during deletion")
    execution_time_ms: Optional[int] = Field(None, description="Operation execution time in milliseconds")
    requires_confirmation: bool = Field(default=False, description="Whether force delete is needed due to existing events")
    events_count: Optional[int] = Field(None, description="Number of delivery events that would be deleted")
    events_deleted: int = Field(default=0, description="Number of delivery events actually deleted")
    safety_warnings: List[str] = Field(default=[], description="Safety warnings about delivery event deletion")
    batch_info: Optional[Dict[str, Any]] = Field(None, description="Batch processing information for large operations")
    
    class Config:
        """Pydantic config."""
        from_attributes = True
        schema_extra = {
            "example": {
                "deleted_count": 2847,
                "campaign_id": "camp_abc123",
                "failed_count": 0,
                "errors": [],
                "operation_type": "campaign",
                "filters_applied": {
                    "status": "failed",
                    "from_date": "2024-01-01T00:00:00Z"
                },
                "execution_time_ms": 3421,
                "requires_confirmation": False,
                "events_count": None,
                "events_deleted": 0,
                "safety_warnings": [],
                "batch_info": {
                    "batches_processed": 3,
                    "batch_size": 1000
                }
            }
        }


class BulkDeleteProgress(BaseModel):
    """Schema for tracking bulk delete operation progress (future use)."""
    operation_id: str = Field(..., description="Unique operation identifier")
    status: str = Field(..., description="Operation status ('pending', 'processing', 'completed', 'failed')")
    progress_percentage: int = Field(..., description="Progress percentage (0-100)")
    messages_processed: int = Field(..., description="Number of messages processed so far")
    total_messages: int = Field(..., description="Total number of messages to process")
    estimated_completion: Optional[datetime] = Field(None, description="Estimated completion time")
    errors: List[str] = Field(default=[], description="Any errors encountered during processing")
    
    class Config:
        """Pydantic config."""
        from_attributes = True



class MessageSendAcceptedResponse(BaseModel):
    """Schema for message send accepted response (202)."""
    status: str = Field(..., description="Request status", example="accepted")
    message: str = Field(..., description="Human-readable status message")
    task_id: str = Field(..., description="Task ID for tracking progress")
    phone_number: str = Field(..., description="Formatted recipient phone number")
    
    class Config:
        """Pydantic config."""
        schema_extra = {
            "example": {
                "status": "accepted",
                "message": "Message queued for sending",
                "task_id": "msg-abc123def456",
                "phone_number": "+1234567890"
            }
        }


class BatchSendAcceptedResponse(BaseModel):
    """Schema for batch send accepted response (202)."""
    status: str = Field(..., description="Request status", example="accepted")
    message: str = Field(..., description="Human-readable status message")
    batch_id: str = Field(..., description="Batch ID for tracking progress")
    total: int = Field(..., description="Total number of messages in batch")
    processed: int = Field(0, description="Number of messages processed (initially 0)")
    successful: int = Field(0, description="Number of successful messages (initially 0)")
    failed: int = Field(0, description="Number of failed messages (initially 0)")
    
    class Config:
        """Pydantic config."""
        schema_extra = {
            "example": {
                "status": "accepted",
                "message": "Batch of 5 messages queued for processing",
                "batch_id": "batch-abc123def456",
                "total": 5,
                "processed": 0,
                "successful": 0,
                "failed": 0
            }
        }