# app/schemas/campaign.py
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, validator


class CampaignBase(BaseModel):
    """Base schema for campaign data."""
    name: str = Field(..., description="Campaign name")
    description: Optional[str] = Field(None, description="Campaign description")
    scheduled_start_at: Optional[datetime] = Field(None, description="Scheduled start time")
    scheduled_end_at: Optional[datetime] = Field(None, description="Scheduled end time")
    settings: Optional[Dict[str, Any]] = Field(default={}, description="Campaign settings")


class CampaignCreate(CampaignBase):
    """Schema for creating a new campaign."""
    pass


class CampaignCreateFromCSV(BaseModel):
    """Schema for creating a campaign from CSV file."""
    name: str = Field(..., description="Campaign name")
    description: Optional[str] = Field(None, description="Campaign description")
    message_template: str = Field(..., description="Message template to send")
    scheduled_start_at: Optional[datetime] = Field(None, description="Scheduled start time")
    scheduled_end_at: Optional[datetime] = Field(None, description="Scheduled end time")
    settings: Optional[Dict[str, Any]] = Field(default={}, description="Campaign settings")


class CampaignUpdate(BaseModel):
    """Schema for updating a campaign."""
    name: Optional[str] = Field(None, description="Campaign name")
    description: Optional[str] = Field(None, description="Campaign description")
    scheduled_start_at: Optional[datetime] = Field(None, description="Scheduled start time")
    scheduled_end_at: Optional[datetime] = Field(None, description="Scheduled end time")
    settings: Optional[Dict[str, Any]] = Field(None, description="Campaign settings")


class CampaignStatus(BaseModel):
    """Schema for campaign status update."""
    status: str = Field(..., description="Campaign status")
    
    @validator("status")
    def validate_status(cls, v):
        """Validate status value."""
        valid_statuses = ["draft", "active", "paused", "completed", "cancelled", "failed"]
        if v not in valid_statuses:
            raise ValueError(f"Status must be one of: {', '.join(valid_statuses)}")
        return v


class CampaignResponse(CampaignBase):
    """Schema for campaign response."""
    id: str = Field(..., description="Campaign ID")
    status: str = Field(..., description="Campaign status")
    total_messages: int = Field(..., description="Total number of messages")
    sent_count: int = Field(..., description="Number of sent messages")
    delivered_count: int = Field(..., description="Number of delivered messages")
    failed_count: int = Field(..., description="Number of failed messages")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    started_at: Optional[datetime] = Field(None, description="Start timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")
    user_id: str = Field(..., description="User ID")
    
    # Add computed fields
    progress_percentage: float = Field(0, description="Progress percentage")
    delivery_success_rate: float = Field(0, description="Delivery success rate")
    
    class Config:
        """Pydantic config."""
        orm_mode = True


class CampaignListResponse(BaseModel):
    """Schema for campaign list response with pagination."""
    items: List[CampaignResponse]
    total: int
    page: int
    size: int
    pages: int