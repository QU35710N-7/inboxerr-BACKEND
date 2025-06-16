"""
API endpoints for SMS message management.
"""
import csv
import io
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


@router.post("/send", response_model=MessageResponse, status_code=202)
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
    """
    # Check rate limits
    await rate_limiter.check_rate_limit(current_user.id, "send_message")
    
    try:
        # Send message asynchronously
        result = await sms_sender.send_message(
            phone_number=message.phone_number,
            message_text=message.message,
            user_id=current_user.id,
            scheduled_at=message.scheduled_at,
            custom_id=message.custom_id,
        )
        
        return result
        
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except SMSGatewayError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending message: {str(e)}")


@router.post("/batch", response_model=BatchMessageResponse, status_code=202)
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
    """
    # Check rate limits - higher limit for batch operations
    await rate_limiter.check_rate_limit(current_user.id, "send_batch")
    
    if not batch.messages:
        raise ValidationError(message="Batch contains no messages")
    
    try:
        # Process batch asynchronously
        result = await sms_sender.send_batch(
            messages=batch.messages,
            user_id=current_user.id,
            options=batch.options,
        )
        
        return result
        
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except SMSGatewayError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing batch: {str(e)}")


@router.post("/import", status_code=202)
async def import_messages(
    file: UploadFile = File(...),
    message_template: str = Query(..., description="Message template to send"),
    delimiter: str = Query(",", description="CSV delimiter"),
    has_header: bool = Query(True, description="Whether CSV has a header row"),
    phone_column: str = Query("phone", description="Column name containing phone numbers"),
    background_tasks: BackgroundTasks = None,
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Import phone numbers from CSV and send messages.
    
    - **file**: CSV file with phone numbers
    - **message_template**: Template for the message to send
    - **delimiter**: CSV delimiter character
    - **has_header**: Whether the CSV has a header row
    - **phone_column**: Column name containing phone numbers (if has_header=True)
    """
    # Check rate limits
    await rate_limiter.check_rate_limit(current_user.id, "import_messages")
    
    try:
        # Read CSV file
        contents = await file.read()
        csv_file = io.StringIO(contents.decode('utf-8'))
        
        # Parse CSV
        csv_reader = csv.reader(csv_file, delimiter=delimiter)
        
        # Skip header if present
        if has_header:
            header = next(csv_reader)
            try:
                phone_index = header.index(phone_column)
            except ValueError:
                raise ValidationError(
                    message=f"Column '{phone_column}' not found in CSV header",
                    details={"available_columns": header}
                )
        else:
            phone_index = 0  # Assume first column has phone numbers
        
        # Extract phone numbers
        phone_numbers = []
        for row in csv_reader:
            if row and len(row) > phone_index:
                phone = row[phone_index].strip()
                if phone:
                    phone_numbers.append(phone)
        
        if not phone_numbers:
            raise ValidationError(message="No valid phone numbers found in CSV")
        
        # Process in background
        task_id = await sms_sender.schedule_batch_from_numbers(
            phone_numbers=phone_numbers,
            message_text=message_template,
            user_id=current_user.id,
        )
        
        return {
            "status": "accepted",
            "message": f"Processing {len(phone_numbers)} messages",
            "task_id": task_id,
        }
        
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error importing messages: {str(e)}")


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
    

@router.delete("/bulk", response_model=BulkDeleteResponse)
async def bulk_delete_messages(
    request: GlobalBulkDeleteRequest,
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Global bulk delete messages by message IDs.
    
    This endpoint efficiently deletes multiple messages by their specific IDs across
    campaigns or for orphaned message cleanup. Designed for edge cases, power user
    operations, and system maintenance tasks.
    
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
    
    Args:
        request: Global bulk delete request with message IDs and confirmation
        current_user: Authenticated user (injected by dependency)
        rate_limiter: Rate limiting for bulk operations (injected by dependency)
    
    Returns:
        BulkDeleteResponse: Detailed results including failed message IDs
        
    Raises:
        HTTPException 400: Invalid request or confirmation missing
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
        
        # Perform global bulk deletion
        async with get_repository_context(MessageRepository) as message_repo:
            # Execute bulk deletion
            deleted_count, failed_message_ids = await message_repo.bulk_delete_messages(
                message_ids=request.message_ids,
                user_id=current_user.id,
                campaign_id=request.campaign_id
            )
            
            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            # Build applied filters for audit trail
            filters_applied = {
                "message_count": len(request.message_ids),
                "unique_messages": len(set(request.message_ids))
            }
            if request.campaign_id:
                filters_applied["campaign_context"] = request.campaign_id
            
            # Build detailed error messages for failed deletions
            error_messages = []
            if failed_message_ids:
                error_messages = [
                    f"Failed to delete message: {msg_id} (not found or not owned by user)" 
                    for msg_id in failed_message_ids
                ]
            
            # Build response
            response = BulkDeleteResponse(
                deleted_count=deleted_count,
                campaign_id=request.campaign_id,
                failed_count=len(failed_message_ids),
                errors=error_messages,
                operation_type="global",
                filters_applied=filters_applied,
                execution_time_ms=execution_time_ms
            )
            
            # Log successful operation for audit
            logger.info(
                f"User {current_user.id} performed global bulk delete of {deleted_count} messages "
                f"in {execution_time_ms}ms. Failed: {len(failed_message_ids)}. "
                f"Campaign context: {request.campaign_id}"
            )
            
            return response
            
    except Exception as e:
        # Log error and return generic error message
        logger.error(f"Error in global bulk_delete_messages: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Error performing global bulk deletion: {str(e)}"
        )