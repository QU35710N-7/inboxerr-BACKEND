"""
API endpoints for SMS message management.
"""
import csv
import io
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, Path, status
import logging


from app.api.v1.dependencies import get_current_user, get_rate_limiter, verify_api_key
from app.core.exceptions import ValidationError, SMSGatewayError, NotFoundError
from app.schemas.message import (
    MessageCreate,
    MessageResponse, 
    BatchMessageRequest, 
    BatchMessageResponse,
    BatchOptions,
    MessageStatus,
    MessageStatusUpdate,
    GlobalBulkDeleteRequest,
    BulkDeleteResponse
)
from app.services.sms.sender import get_sms_sender
from app.schemas.user import User
from app.utils.pagination import PaginationParams, paginate_response
from app.utils.pagination import PaginatedResponse
from app.db.session import get_repository_context

router = APIRouter()
logger = logging.getLogger("inboxerr.endpoint")

# ===========================
# COLLECTION OPERATIONS
# ===========================

@router.get("/", response_model=PaginatedResponse[MessageResponse])
async def list_messages(
    pagination: PaginationParams = Depends(),
    status: Optional[str] = Query(None, description="Filter by message status"),
    phone_number: Optional[str] = Query(None, description="Filter by phone number"),
    from_date: Optional[str] = Query(None, description="Filter from date (ISO format)"),
    to_date: Optional[str] = Query(None, description="Filter to date (ISO format)"),
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
):
    """
    List messages with optional filtering.
    
    Returns a paginated list of messages for the current user.
    """
    try:
        filters = {
            "status": status,
            "phone_number": phone_number,
            "from_date": from_date,
            "to_date": to_date,
            "user_id": current_user.id
        }
        
        # Get messages with pagination
        messages, total = await sms_sender.list_messages(
            filters=filters,
            skip=pagination.skip,
            limit=pagination.limit
        )
        
        # Return paginated response
        return paginate_response(
            items=messages,
            total=total,
            pagination=pagination
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing messages: {str(e)}")

@router.post("/send", response_model=dict, status_code=202)
async def send_message(
    message: MessageCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Send a single SMS message.
    
    - **phone_number**: Recipient phone number in E.164 format (e.g., +1234567890)
    - **message**: Content of the SMS message
    - **scheduled_at**: Optional timestamp to schedule the message for future delivery
    
    Returns 202 immediately with task_id for tracking progress.
    """
    # Check rate limits
    await rate_limiter.check_rate_limit(current_user.id, "send_message")
    
    # Validate phone number early (before background task)
    from app.utils.phone import validate_phone
    is_valid, formatted_number, error, _ = validate_phone(message.phone_number)
    if not is_valid:
        raise HTTPException(status_code=422, detail=f"Invalid phone number: {error}")
    
    # Generate task ID for tracking
    from app.utils.ids import generate_prefixed_id, IDPrefix
    task_id = generate_prefixed_id(IDPrefix.TASK) # 	Merely a UUID you log so you can match logs to HTTP requests. You’re not using it elsewhere.
    
    # Add to background tasks - this returns immediately
    background_tasks.add_task(
        _send_message_background,
        sms_sender=sms_sender,
        phone_number=message.phone_number,
        message_text=message.message,
        user_id=current_user.id,
        scheduled_at=message.scheduled_at,
        custom_id=message.custom_id,
        task_id=task_id
    )
    
    # Return 202 immediately
    return {
        "status": "accepted",
        "message": "Message queued for sending",
        "task_id": task_id,
        "phone_number": formatted_number
    }


@router.post("/batch", response_model=dict, status_code=202)
async def send_batch(
    batch: BatchMessageRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Send a batch of SMS messages.
    
    - **messages**: List of messages to send
    - **options**: Optional batch processing options
    
    Returns 202 immediately with batch_id for tracking progress.
    """
    # Check rate limits - higher limit for batch operations
    await rate_limiter.check_rate_limit(current_user.id, "send_batch")
    
    if not batch.messages:
        raise ValidationError(message="Batch contains no messages")
    
    # Validate messages early (before background task)
    from app.utils.phone import validate_phone
    invalid_numbers = []
    for i, msg in enumerate(batch.messages):
        is_valid, _, error, _ = validate_phone(msg.phone_number)
        if not is_valid:
            invalid_numbers.append(f"Message {i}: {error}")
    
    if invalid_numbers:
        raise HTTPException(
            status_code=422, 
            detail=f"Invalid phone numbers: {'; '.join(invalid_numbers[:3])}"
        )
    
    # Generate batch/task ID for tracking
    from app.utils.ids import generate_prefixed_id, IDPrefix
    batch_id = generate_prefixed_id(IDPrefix.BATCH)
    task_id = generate_prefixed_id(IDPrefix.TASK) #	Merely a UUID you log so you can match logs to HTTP requests. You’re not using it elsewhere.
    
    # Add to background tasks - this returns immediately
    background_tasks.add_task(
        _send_batch_background,
        sms_sender=sms_sender,
        messages=batch.messages,
        user_id=current_user.id,
        options=batch.options,
        batch_id=batch_id,
        task_id=task_id
    )
    
    # Return 202 immediately
    return {
        "status": "accepted", 
        "message": f"Batch of {len(batch.messages)} messages queued for processing",
        "batch_id": batch_id,
        "task_id": task_id,
        "total": len(batch.messages),
        "processed": 0,
        "successful": 0,
        "failed": 0
    }


@router.delete("/bulk", response_model=BulkDeleteResponse)
async def bulk_delete_messages(
    request: GlobalBulkDeleteRequest,
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Global bulk delete messages by message IDs with event safety.
    
    This endpoint efficiently deletes multiple messages by their specific IDs across
    campaigns or for orphaned message cleanup. Enhanced with delivery event safety
    checking for edge cases, power user operations, and system maintenance tasks.
    
    **Event Safety Features:**
    - Pre-deletion check for delivery events
    - Requires explicit confirmation to delete tracking data
    - Clear warnings about analytics data loss
    - Two-phase deletion (events first, then messages)
    
    **Safety Features:**
    - User authorization (can only delete own messages)
    - Smaller batch limits (1K vs 10K for campaign-scoped)
    - Optional campaign context validation
    - Detailed failure reporting for partial operations
    - Comprehensive audit logging
    
    **Use Cases:**
    - Cross-campaign message cleanup by power users
    - Frontend multi-select bulk operations
    - Orphaned message removal during maintenance
    - Compliance-driven deletion by specific message IDs
    - System administration tasks
    
    **Performance:**
    - Handles up to 1K message deletions efficiently
    - Uses optimized IN clause with PostgreSQL
    - Smaller batches for safety vs campaign operations
    - Batched processing prevents server overload
    
    Args:
        request: Global bulk delete request with message IDs, confirmation, and force options
        current_user: Authenticated user (injected by dependency)
        rate_limiter: Rate limiting for bulk operations (injected by dependency)
    
    Returns:
        BulkDeleteResponse: Detailed results including event safety information
        
    Raises:
        HTTPException 400: Invalid request or missing confirmation
        HTTPException 403: User not authorized for specified messages
        HTTPException 429: Rate limit exceeded
        HTTPException 500: Database or internal server error
    """
    import time
    
    # Check rate limits for global bulk operations
    await rate_limiter.check_rate_limit(current_user.id, "bulk_delete_global")
    
    start_time = time.time()
    
    try:
        # Use repository context for proper connection management
        from app.db.repositories.messages import MessageRepository
        
        # Perform global bulk deletion with event safety
        async with get_repository_context(MessageRepository) as message_repo:
            # Execute bulk deletion with enhanced safety
            deleted_count, failed_message_ids, metadata = await message_repo.bulk_delete_messages(
                message_ids=request.message_ids,
                user_id=current_user.id,
                campaign_id=request.campaign_id,
                force_delete=request.force_delete
            )
            
            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            # Build applied filters for audit trail
            filters_applied = {
                "message_count": len(request.message_ids),
                "unique_messages": len(set(request.message_ids)),
                "force_delete": request.force_delete
            }
            if request.campaign_id:
                filters_applied["campaign_context"] = request.campaign_id
            
            # Build detailed error messages for failed deletions
            error_messages = []
            if failed_message_ids:
                if metadata.get("requires_confirmation"):
                    # Failed due to event safety check
                    error_messages = metadata.get("safety_warnings", [])
                else:
                    # Failed due to other reasons (not found, not owned, etc.)
                    error_messages = [
                        f"Failed to delete message: {msg_id} (not found or not owned by user)" 
                        for msg_id in failed_message_ids
                    ]
            
            # Build response with enhanced metadata
            response = BulkDeleteResponse(
                deleted_count=deleted_count,
                campaign_id=request.campaign_id,
                failed_count=len(failed_message_ids),
                errors=error_messages,
                operation_type="global",
                filters_applied=filters_applied,
                execution_time_ms=execution_time_ms,
                requires_confirmation=metadata.get("requires_confirmation", False),
                events_count=metadata.get("events_count"),
                events_deleted=metadata.get("events_deleted", 0),
                safety_warnings=metadata.get("safety_warnings", []),
                batch_info=None  # Global operations use smaller fixed batches
            )
            
            # Log successful operation for audit
            logger.info(
                f"User {current_user.id} performed global bulk delete of {deleted_count} messages "
                f"in {execution_time_ms}ms. Failed: {len(failed_message_ids)}. "
                f"Campaign context: {request.campaign_id}. Events deleted: {metadata.get('events_deleted', 0)}. "
                f"Force delete: {request.force_delete}"
            )
            
            return response
            
    except Exception as e:
        # Log error and return generic error message
        logger.error(f"Error in global bulk_delete_messages: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Error performing global bulk deletion: {str(e)}"
        )

# ===========================
# NESTED RESOURCES SECTION
# ===========================

@router.get("/tasks/{task_id}", response_model=dict)
async def get_task_status(
    task_id: str = Path(..., description="Task ID"),
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
):
    """
    Check the status of a background task.
    
    Used for tracking progress of batch operations and imports.
    """
    try:
        task_status = await sms_sender.get_task_status(task_id, user_id=current_user.id)
        if not task_status:
            raise NotFoundError(message=f"Task {task_id} not found")
        
        return task_status
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving task status: {str(e)}")
    

# ===========================
# INDIVIDUAL RESOURCES (ALWAYS LAST)
# ===========================


@router.get("/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: str = Path(..., description="Message ID"),
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
):
    """
    Get details of a specific message.
    """
    try:
        message = await sms_sender.get_message(message_id, user_id=current_user.id)
        if not message:
            raise NotFoundError(message=f"Message {message_id} not found")
        return message
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving message: {str(e)}")



@router.put("/{message_id}/status", response_model=MessageResponse)
async def update_message_status(
    status_update: MessageStatusUpdate,
    message_id: str = Path(..., description="Message ID"),
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
):
    """
    Update the status of a message.
    
    This is primarily for administrative purposes or handling external status updates.
    """
    try:
        # Verify the user has permission to update this message
        message = await sms_sender.get_message(message_id, user_id=current_user.id)
        if not message:
            raise NotFoundError(message=f"Message {message_id} not found")
        
        # Update the status
        updated_message = await sms_sender.update_message_status(
            message_id=message_id,
            status=status_update.status,
            reason=status_update.reason,
            user_id=current_user.id
        )
        
        return updated_message
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating message status: {str(e)}")


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: str = Path(..., description="Message ID"),
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
):
    """
    Delete a message.
    
    This will only remove it from the database, but cannot recall messages already sent.
    """
    try:
        # Verify the message exists and belongs to the user
        message = await sms_sender.get_message(message_id, user_id=current_user.id)
        if not message:
            raise NotFoundError(message=f"Message {message_id} not found")
        
        # Delete the message
        success = await sms_sender.delete_message(message_id, user_id=current_user.id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete message")
        
        return None  # No content response
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting message: {str(e)}")


# Background task functions
async def _send_message_background(
    sms_sender,
    phone_number: str,
    message_text: str,
    user_id: str,
    scheduled_at: Optional[datetime] = None,
    custom_id: Optional[str] = None,
    task_id: Optional[str] = None
):
    """
    Background task for sending a single message.
    """
    try:
        result = await sms_sender.send_message(
            phone_number=phone_number,
            message_text=message_text,
            user_id=user_id,
            scheduled_at=scheduled_at,
            custom_id=custom_id,
        )
        logger.info(f"Background message send completed for task {task_id}: {result.get('id', 'unknown')}")
    except Exception as e:
        logger.error(f"Background message send failed for task {task_id}: {str(e)}")

async def _send_batch_background(
    sms_sender,
    messages: List[MessageCreate],
    user_id: str,
    options: Optional[BatchOptions] = None,
    batch_id: Optional[str] = None,
    task_id: Optional[str] = None,
):
    """
    Background task for sending a batch of messages.
    """
    try:
        result = await sms_sender.send_batch(
            messages=messages,
            user_id=user_id,
            options=options,
            batch_id=batch_id
        )
        logger.info(f"Background batch send completed for task {task_id} (batch {batch_id}): {result.get('batch_id', 'unknown')}")
    except Exception as e:
        logger.error(f"Background batch send failed for task {task_id} (batch {batch_id}): {str(e)}")