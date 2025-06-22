# app/schemas/campaign.py
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from uuid import UUID
from enum import Enum

from pydantic import BaseModel, Field, validator



class CampaignStatus(str, Enum):
    """
    Campaign status enum.
    
    Defines all possible states a campaign can be in during its lifecycle.
    Uses string enum to ensure JSON serialization compatibility and type safety.
    """
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CampaignBase(BaseModel):
    """Base schema for campaign data."""
    name: str = Field(..., description="Campaign name")
    description: Optional[str] = Field(None, description="Campaign description")
    scheduled_start_at: Optional[datetime] = Field(None, description="Scheduled start time")
    scheduled_end_at: Optional[datetime] = Field(None, description="Scheduled end time")
    settings: Optional[Dict[str, Any]] = Field(default={}, description="Campaign settings")
    # Personalization fields
    message_content: Optional[str] = Field(None, description="Message content for personalization")
    template_id: Optional[str] = Field(None, description="Template ID if using a template")


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
    #Personalization fields
    message_content: Optional[str] = Field(None, description="Message content for personalization")
    template_id: Optional[str] = Field(None, description="Template ID if using a template")


class CampaignResponse(CampaignBase):
    """Schema for campaign response."""
    id: str = Field(..., description="Campaign ID")
    status: CampaignStatus = Field(..., description="Campaign status")
    total_messages: int = Field(..., description="Total number of messages")
    sent_count: int = Field(..., description="Number of sent messages")
    delivered_count: int = Field(..., description="Number of delivered messages")
    failed_count: int = Field(..., description="Number of failed messages")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    started_at: Optional[datetime] = Field(None, description="Start timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")
    user_id: str = Field(..., description="User ID")

    # Personalization fields
    message_content: Optional[str] = Field(None, description="Message content for personalization")
    template_id: Optional[str] = Field(None, description="Template ID if using a template")
    
    
    # Add computed fields
    progress_percentage: float = Field(0, description="Progress percentage")
    delivery_success_rate: float = Field(0, description="Delivery success rate")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


