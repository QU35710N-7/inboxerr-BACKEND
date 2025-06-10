"""
Pydantic schemas for metrics-related API operations.
"""
from typing import List, Dict, Any
from datetime import date
from pydantic import BaseModel, Field


class PeriodInfo(BaseModel):
    """Schema for period information."""
    start_date: str = Field(..., description="Start date in ISO format")
    end_date: str = Field(..., description="End date in ISO format")


class MessageMetrics(BaseModel):
    """Schema for message metrics."""
    sent: int = Field(..., description="Number of sent messages")
    delivered: int = Field(..., description="Number of delivered messages")
    failed: int = Field(..., description="Number of failed messages")
    delivery_rate: float = Field(..., description="Delivery rate percentage")


class CampaignMetrics(BaseModel):
    """Schema for campaign metrics."""
    created: int = Field(..., description="Number of created campaigns")
    completed: int = Field(..., description="Number of completed campaigns")
    active: int = Field(..., description="Number of active campaigns")


class TemplateMetrics(BaseModel):
    """Schema for template metrics."""
    created: int = Field(..., description="Number of created templates")
    used: int = Field(..., description="Number of times templates were used")


class QuotaMetrics(BaseModel):
    """Schema for quota metrics."""
    used: int = Field(..., description="Number of messages used from quota")
    total: int = Field(..., description="Total quota limit")
    percent: float = Field(..., description="Percentage of quota used")


class DailyData(BaseModel):
    """Schema for daily metrics data."""
    date: str = Field(..., description="Date in ISO format")
    sent: int = Field(..., description="Messages sent on this date")
    delivered: int = Field(..., description="Messages delivered on this date")
    failed: int = Field(..., description="Messages failed on this date")


class MetricsSummary(BaseModel):
    """Schema for metrics summary."""
    period: PeriodInfo = Field(..., description="Period information")
    messages: MessageMetrics = Field(..., description="Message metrics")
    campaigns: CampaignMetrics = Field(..., description="Campaign metrics")
    templates: TemplateMetrics = Field(..., description="Template metrics")
    quota: QuotaMetrics = Field(..., description="Quota metrics")


class DashboardMetricsResponse(BaseModel):
    """Schema for dashboard metrics response."""
    summary: MetricsSummary = Field(..., description="Summary metrics")
    daily_data: List[DailyData] = Field(..., description="Daily metrics data for charts")
    period: str = Field(..., description="Requested period")

    class Config:
        """Pydantic config."""
        from_attributes = True


class UsageMetricsResponse(BaseModel):
    """Schema for usage metrics response."""
    message_count: int = Field(..., description="Total message count")
    delivery_rate: float = Field(..., description="Overall delivery rate")
    quota: QuotaMetrics = Field(..., description="Quota information")

    class Config:
        """Pydantic config."""
        from_attributes = True


class SystemMessageMetrics(BaseModel):
    """Schema for system-wide message metrics."""
    total: int = Field(..., description="Total messages in system")
    sent: int = Field(..., description="Total sent messages")
    delivered: int = Field(..., description="Total delivered messages")
    failed: int = Field(..., description="Total failed messages")
    last_24h: int = Field(..., description="Messages in last 24 hours")


class SystemUserMetrics(BaseModel):
    """Schema for system-wide user metrics."""
    total: int = Field(..., description="Total users in system")
    active: int = Field(..., description="Active users")
    new_today: int = Field(..., description="New users today")


class SystemCampaignMetrics(BaseModel):
    """Schema for system-wide campaign metrics."""
    total: int = Field(..., description="Total campaigns in system")
    active: int = Field(..., description="Active campaigns")
    completed_today: int = Field(..., description="Campaigns completed today")


class SystemMetricsResponse(BaseModel):
    """Schema for system metrics response (admin only)."""
    messages: SystemMessageMetrics = Field(..., description="System message metrics")
    users: SystemUserMetrics = Field(..., description="System user metrics")
    campaigns: SystemCampaignMetrics = Field(..., description="System campaign metrics")

    class Config:
        """Pydantic config."""
        from_attributes = True