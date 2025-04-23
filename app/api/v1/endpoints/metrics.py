"""
API endpoints for metrics and reporting.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import get_current_user
from app.schemas.user import User

router = APIRouter()

@router.get("/")
async def get_metrics(
    current_user: User = Depends(get_current_user)
):
    """
    Get system metrics and statistics.
    """
    # This is a stub - implementation will be added later
    return {
        "message_count": {
            "total": 0,
            "sent": 0,
            "delivered": 0,
            "failed": 0
        },
        "user_count": 1,
        "webhook_count": 0
    }

@router.get("/usage")
async def get_usage_metrics(
    current_user: User = Depends(get_current_user)
):
    """
    Get usage metrics for the current user.
    """
    # This is a stub - implementation will be added later
    return {
        "message_count": 0,
        "quota": {
            "used": 0,
            "total": 1000
        }
    }