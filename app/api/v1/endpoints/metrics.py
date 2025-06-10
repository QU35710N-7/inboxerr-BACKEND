"""
API endpoints for metrics and reporting.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Any, Optional

from app.api.v1.dependencies import get_current_user
from app.schemas.user import User
from app.schemas.metrics import (
    DashboardMetricsResponse, 
    UsageMetricsResponse,
    SystemMetricsResponse
)
from app.services.metrics.collector import get_user_metrics, get_system_metrics

router = APIRouter()


@router.get("/", response_model=SystemMetricsResponse)
async def get_metrics(
    current_user: User = Depends(get_current_user)
):
    """
    Get system metrics and statistics.
    Admin users get system-wide metrics, regular users get their own metrics.
    """
    if current_user.role == "admin":
        # Admins get system-wide metrics
        return await get_system_metrics()
    else:
        # Regular users get their dashboard metrics instead
        user_metrics = await get_user_metrics(current_user.id)
        
        # Transform user metrics to system format for consistency
        return {
            "messages": {
                "total": user_metrics["summary"]["messages"]["sent"],
                "sent": user_metrics["summary"]["messages"]["sent"],
                "delivered": user_metrics["summary"]["messages"]["delivered"],
                "failed": user_metrics["summary"]["messages"]["failed"],
                "last_24h": 0  # User-specific doesn't have this breakdown
            },
            "users": {
                "total": 1,  # Just the current user
                "active": 1,
                "new_today": 0
            },
            "campaigns": {
                "total": user_metrics["summary"]["campaigns"]["created"],
                "active": user_metrics["summary"]["campaigns"]["active"],
                "completed_today": 0
            }
        }


@router.get("/usage", response_model=UsageMetricsResponse)
async def get_usage_metrics(
    current_user: User = Depends(get_current_user)
):
    """
    Get usage metrics for the current user.
    """
    user_metrics = await get_user_metrics(current_user.id)
    
    # Format for the usage endpoint
    return {
        "message_count": user_metrics["summary"]["messages"]["sent"],
        "delivery_rate": user_metrics["summary"]["messages"]["delivery_rate"],
        "quota": {
            "used": user_metrics["summary"]["quota"]["used"],
            "total": user_metrics["summary"]["quota"]["total"],
            "percent": user_metrics["summary"]["quota"]["percent"]
        }
    }


@router.get("/dashboard", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(
    period: str = Query("week", description="Time period: day, week, month, year"),
    current_user: User = Depends(get_current_user)
):
    """
    Get metrics formatted for dashboard display.
    """
    metrics = await get_user_metrics(current_user.id, period=period)
    
    # Return data already formatted for dashboard
    return metrics