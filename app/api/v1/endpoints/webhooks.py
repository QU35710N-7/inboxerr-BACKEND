# app/api/v1/endpoints/webhooks.py
"""
API endpoints for webhook management.
"""
import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Body, Header, status

from app.api.v1.dependencies import get_current_user
from app.schemas.user import User
from app.services.webhooks.manager import process_gateway_webhook

router = APIRouter()
logger = logging.getLogger("inboxerr.webhooks")

@router.get("/")
async def list_webhooks(
    current_user: User = Depends(get_current_user)
):
    """
    List all webhooks for the current user.
    """
    # This is a stub - implementation will be added later
    return {"message": "Webhook listing not implemented yet"}

@router.post("/gateway", status_code=status.HTTP_200_OK)
async def webhook_receiver(
    request: Request,
    x_signature: str = Header(None),
    x_timestamp: str = Header(None)
):
    """
    Receive webhooks from the SMS Gateway.
    
    This endpoint is called by the SMS Gateway when events occur.
    No authentication is required as we validate using signatures.
    """
    # Get raw body for signature validation
    body = await request.body()
    
    # Prepare headers for signature verification
    headers = {
        "X-Signature": x_signature,
        "X-Timestamp": x_timestamp
    }
    
    # Process the webhook
    success, result = await process_gateway_webhook(body, headers)
    
    if not success:
        logger.error(f"Error processing webhook: {result.get('error')}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get('error', 'Error processing webhook')
        )
    
    return result