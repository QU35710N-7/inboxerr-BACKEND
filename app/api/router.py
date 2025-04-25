"""
Main API router that includes all endpoint routers.
"""
from fastapi import APIRouter

from app.api.v1.endpoints import auth, messages, webhooks, metrics, campaigns, templates


# Create main API router
api_router = APIRouter()

# Include all endpoint routers with appropriate tags
api_router.include_router(
    auth.router, 
    prefix="/auth", 
    tags=["Authentication"]
)
api_router.include_router(
    messages.router, 
    prefix="/messages", 
    tags=["Messages"]
)
api_router.include_router(
    campaigns.router, 
    prefix="/campaigns", 
    tags=["Campaigns"]
)
api_router.include_router(
    templates.router, 
    prefix="/templates", 
    tags=["Templates"]
)
api_router.include_router(
    webhooks.router, 
    prefix="/webhooks", 
    tags=["Webhooks"]
)
api_router.include_router(
    metrics.router, 
    prefix="/metrics", 
    tags=["Metrics"]
)