# app/api/v1/endpoints/campaigns.py
import csv
import io
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, Path, status
from fastapi.responses import JSONResponse
import logging

from app.api.v1.dependencies import get_current_user, get_rate_limiter
from app.core.exceptions import ValidationError, NotFoundError
from app.schemas.campaign import (
    CampaignCreate,
    CampaignCreateFromCSV,
    CampaignUpdate,
    CampaignResponse,
    CampaignStatus,
)
from app.schemas.user import User
from app.schemas.message import MessageResponse, CampaignBulkDeleteRequest, BulkDeleteResponse
from app.utils.pagination import PaginationParams, paginate_response, PaginatedResponse, PageInfo
from app.services.campaigns.processor import get_campaign_processor
from app.db.session import get_repository_context

router = APIRouter()
logger = logging.getLogger("inboxerr.endpoint")



@router.post("/", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    campaign: CampaignCreate,
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Create a new campaign.
    
    This creates a campaign in draft status. Messages can be added later.
    """
    # Check rate limits
    await rate_limiter.check_rate_limit(current_user.id, "create_campaign")
    
    try:
        # Use repository context for proper connection management
        from app.db.repositories.campaigns import CampaignRepository
        
        async with get_repository_context(CampaignRepository) as campaign_repo:
            # Create campaign
            result = await campaign_repo.create_campaign(
                name=campaign.name,
                description=campaign.description,
                user_id=current_user.id,
                scheduled_start_at=campaign.scheduled_start_at,
                scheduled_end_at=campaign.scheduled_end_at,
                settings=campaign.settings
            )
            
            return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating campaign: {str(e)}")


@router.post("/from-csv", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign_from_csv(
    file: UploadFile = File(...),
    campaign_data: str = Query(..., description="Campaign data as JSON string"),
    delimiter: str = Query(",", description="CSV delimiter"),
    has_header: bool = Query(True, description="Whether CSV has a header row"),
    phone_column: str = Query("phone", description="Column name containing phone numbers"),
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Create a campaign and add phone numbers from CSV.
    
    This creates a campaign and immediately adds all phone numbers from the CSV.
    The campaign will remain in draft status until explicitly started.
    """
    import json
    
    # Check rate limits
    await rate_limiter.check_rate_limit(current_user.id, "create_campaign")
    
    try:
        # Parse campaign data
        campaign_dict = json.loads(campaign_data)
        campaign_data = CampaignCreateFromCSV(**campaign_dict)
        
        # Use repository context for proper connection management
        from app.db.repositories.campaigns import CampaignRepository
        
        async with get_repository_context(CampaignRepository) as campaign_repo:
            # Create campaign
            campaign = await campaign_repo.create_campaign(
                name=campaign_data.name,
                description=campaign_data.description,
                user_id=current_user.id,
                scheduled_start_at=campaign_data.scheduled_start_at,
                scheduled_end_at=campaign_data.scheduled_end_at,
                settings=campaign_data.settings
            )
            
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
            
            # Add phone numbers to campaign
            added_count = await campaign_repo.add_messages_to_campaign(
                campaign_id=campaign.id,
                phone_numbers=phone_numbers,
                message_text=campaign_data.message_template,
                user_id=current_user.id
            )
            
            # Refresh campaign
            campaign = await campaign_repo.get_by_id(campaign.id)
            
            return campaign
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid campaign data JSON")
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating campaign: {str(e)}")


@router.get("/", response_model=PaginatedResponse[CampaignResponse])
async def list_campaigns(
    pagination: PaginationParams = Depends(),
    status: Optional[str] = Query(None, description="Filter by campaign status"),
    current_user: User = Depends(get_current_user),
):
    """
    List campaigns for the current user.
    
    Returns a paginated list of campaigns.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.campaigns import CampaignRepository
        
        async with get_repository_context(CampaignRepository) as campaign_repo:
            # Get campaigns with pagination
            campaigns, total = await campaign_repo.get_campaigns_for_user(
                user_id=current_user.id,
                status=status,
                skip=pagination.skip,
                limit=pagination.limit
            )
            
            # Calculate pagination info
            total_pages = (total + pagination.limit - 1) // pagination.limit
            
            # Return paginated response
            return paginate_response(campaigns, total, pagination)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing campaigns: {str(e)}")


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
   campaign_id: str = Path(..., description="Campaign ID"),
   current_user: User = Depends(get_current_user),
):
   """
   Get details of a specific campaign.
   """
   try:
       from app.db.repositories.campaigns import CampaignRepository
       
       async with get_repository_context(CampaignRepository) as campaign_repo:
           # Get campaign
           campaign = await campaign_repo.get_by_id(campaign_id)
           if not campaign:
               raise NotFoundError(message=f"Campaign {campaign_id} not found")
           
           # Check authorization
           if campaign.user_id != current_user.id:
               raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
           
           return campaign
       
   except NotFoundError as e:
       raise HTTPException(status_code=404, detail=str(e))
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error retrieving campaign: {str(e)}")


@router.put("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
   campaign_update: CampaignUpdate,
   campaign_id: str = Path(..., description="Campaign ID"),
   current_user: User = Depends(get_current_user),
):
   """
   Update campaign details.
   
   Only draft campaigns can be fully updated. Active campaigns can only have their description updated.
   """
   try:
       from app.db.repositories.campaigns import CampaignRepository
       
       async with get_repository_context(CampaignRepository) as campaign_repo:
           # Get campaign
           campaign = await campaign_repo.get_by_id(campaign_id)
           if not campaign:
               raise NotFoundError(message=f"Campaign {campaign_id} not found")
           
           # Check authorization
           if campaign.user_id != current_user.id:
               raise HTTPException(status_code=403, detail="Not authorized to update this campaign")
           
           # Check if campaign can be updated
           if campaign.status != "draft" and any([
               campaign_update.scheduled_start_at is not None,
               campaign_update.scheduled_end_at is not None,
               campaign_update.name is not None
           ]):
               raise HTTPException(
                   status_code=400, 
                   detail="Only draft campaigns can have name or schedule updated"
               )
           
           # Convert to dict and remove None values
           update_data = {k: v for k, v in campaign_update.dict().items() if v is not None}
           
           # Update campaign
           updated = await campaign_repo.update(id=campaign_id, obj_in=update_data)
           if not updated:
               raise HTTPException(status_code=500, detail="Failed to update campaign")
           
           return updated
       
   except NotFoundError as e:
       raise HTTPException(status_code=404, detail=str(e))
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error updating campaign: {str(e)}")


@router.post("/{campaign_id}/start", response_model=CampaignResponse)
async def start_campaign(
   campaign_id: str = Path(..., description="Campaign ID"),
   current_user: User = Depends(get_current_user),
   campaign_processor = Depends(get_campaign_processor),
):
   """
   Start a campaign.
   
   This will change the campaign status to active and begin sending messages.
   """
   try:
       # Start campaign - campaign_processor already uses context managers internally
       success = await campaign_processor.start_campaign(
           campaign_id=campaign_id,
           user_id=current_user.id
       )
       
       if not success:
           raise HTTPException(status_code=400, detail="Failed to start campaign")
       
       # Get updated campaign - use context manager for this separate operation
       from app.db.repositories.campaigns import CampaignRepository
       
       async with get_repository_context(CampaignRepository) as campaign_repo:
           campaign = await campaign_repo.get_by_id(campaign_id)
           return campaign
       
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error starting campaign: {str(e)}")


@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
async def pause_campaign(
   campaign_id: str = Path(..., description="Campaign ID"),
   current_user: User = Depends(get_current_user),
   campaign_processor = Depends(get_campaign_processor),
):
   """
   Pause a campaign.
   
   This will change the campaign status to paused and stop sending messages.
   The campaign can be resumed later.
   """
   try:
       # Pause campaign - campaign_processor already uses context managers internally
       success = await campaign_processor.pause_campaign(
           campaign_id=campaign_id,
           user_id=current_user.id
       )
       
       if not success:
           raise HTTPException(status_code=400, detail="Failed to pause campaign")
       
       # Get updated campaign - use context manager for this separate operation
       from app.db.repositories.campaigns import CampaignRepository
       
       async with get_repository_context(CampaignRepository) as campaign_repo:
           campaign = await campaign_repo.get_by_id(campaign_id)
           return campaign
       
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error pausing campaign: {str(e)}")


@router.post("/{campaign_id}/cancel", response_model=CampaignResponse)
async def cancel_campaign(
   campaign_id: str = Path(..., description="Campaign ID"),
   current_user: User = Depends(get_current_user),
   campaign_processor = Depends(get_campaign_processor),
):
   """
   Cancel a campaign.
   
   This will change the campaign status to cancelled and stop sending messages.
   The campaign cannot be resumed after cancellation.
   """
   try:
       # Cancel campaign - campaign_processor already uses context managers internally
       success = await campaign_processor.cancel_campaign(
           campaign_id=campaign_id,
           user_id=current_user.id
       )
       
       if not success:
           raise HTTPException(status_code=400, detail="Failed to cancel campaign")
       
       # Get updated campaign - use context manager for this separate operation
       from app.db.repositories.campaigns import CampaignRepository
       
       async with get_repository_context(CampaignRepository) as campaign_repo:
           campaign = await campaign_repo.get_by_id(campaign_id)
           return campaign
       
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error cancelling campaign: {str(e)}")


@router.get("/{campaign_id}/messages", response_model=PaginatedResponse[MessageResponse])
async def get_campaign_messages(
   campaign_id: str = Path(..., description="Campaign ID"),
   pagination: PaginationParams = Depends(),
   status: Optional[str] = Query(None, description="Filter by message status"),
   current_user: User = Depends(get_current_user),
):
   """
   Get messages for a campaign.
   
   Returns a paginated list of messages for the specified campaign.
   """
   try:
       # Use repository context for proper connection management
       from app.db.repositories.campaigns import CampaignRepository
       from app.db.repositories.messages import MessageRepository
       
       # First check if campaign exists and belongs to user
       async with get_repository_context(CampaignRepository) as campaign_repo:
           campaign = await campaign_repo.get_by_id(campaign_id)
           
           if not campaign:
               raise NotFoundError(message=f"Campaign {campaign_id} not found")
           
           if campaign.user_id != current_user.id:
               raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
       
       # Get messages for campaign - in a separate context to avoid long transactions
       async with get_repository_context(MessageRepository) as message_repo:
           messages, total = await message_repo.get_messages_for_campaign(
               campaign_id=campaign_id,
               status=status,
               skip=pagination.skip,
               limit=pagination.limit
           )

           # Calculate proper pagination info
           total_pages = (total + pagination.limit - 1) // pagination.limit

           # Create proper PageInfo object
           page_info = PageInfo(
               current_page=pagination.page,
               total_pages=total_pages,
               page_size=pagination.limit,
               total_items=total,
               has_previous=pagination.page > 1,
               has_next=pagination.page < total_pages
           )
           
           # Return PaginatedResponse object
           return PaginatedResponse(
               items=messages,  # Direct Message objects - FastAPI will serialize them
               page_info=page_info
           )
       
   except NotFoundError as e:
       raise HTTPException(status_code=404, detail=str(e))
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error retrieving campaign messages: {str(e)}")


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(
   campaign_id: str = Path(..., description="Campaign ID"),
   current_user: User = Depends(get_current_user),
):
   """
   Delete a campaign.
   
   Only draft campaigns can be deleted. Active, paused, or completed campaigns cannot be deleted.
   """
   try:
       from app.db.repositories.campaigns import CampaignRepository
       
       async with get_repository_context(CampaignRepository) as campaign_repo:
           # Get campaign
           campaign = await campaign_repo.get_by_id(campaign_id)
           if not campaign:
               raise NotFoundError(message=f"Campaign {campaign_id} not found")
           
           # Check authorization
           if campaign.user_id != current_user.id:
               raise HTTPException(status_code=403, detail="Not authorized to delete this campaign")
           
           # Check if campaign can be deleted
           if campaign.status != "draft":
               raise HTTPException(
                   status_code=400, 
                   detail="Only draft campaigns can be deleted"
               )
           
           # Delete campaign
           success = await campaign_repo.delete(id=campaign_id)
           if not success:
               raise HTTPException(status_code=500, detail="Failed to delete campaign")
           
           return JSONResponse(status_code=204, content=None)
       
   except NotFoundError as e:
       raise HTTPException(status_code=404, detail=str(e))
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error deleting campaign: {str(e)}")
   

@router.delete("/{campaign_id}/messages/bulk", response_model=BulkDeleteResponse)
async def bulk_delete_campaign_messages(
    request: CampaignBulkDeleteRequest,
    campaign_id: str = Path(..., description="Campaign ID"),
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
):
    """
    Bulk delete messages from a campaign with event safety and server stability.
    
    This endpoint efficiently deletes multiple messages belonging to a specific campaign
    with optional filtering by status and date range. Enhanced with delivery event safety
    checking and batched processing for high-volume operations handling 10K-30K messages.
    
    **Event Safety Features:**
    - Pre-deletion check for delivery events
    - Requires explicit confirmation to delete tracking data
    - Clear warnings about data loss implications
    - Two-phase deletion (events first, then messages)
    
    **Server Stability Features:**
    - Batched processing prevents server overload
    - Configurable batch sizes for different load scenarios
    - Inter-batch delays prevent database lock contention
    - Graceful handling of partial failures
    
    **Business Safety Features:**
    - Campaign ownership validation
    - User authorization checks  
    - Active campaign protection
    - Comprehensive audit logging
    
    **Performance:**
    - Single SQL query per batch
    - Optimized for large datasets
    - 30K deletions in batches for stability
    
    **Business Use Cases:**
    - Clean up failed messages from campaigns
    - Remove test messages before campaign launch
    - Compliance-driven message deletion (with event confirmation)
    - Campaign optimization and cleanup
    
    Args:
        campaign_id: ID of the campaign containing messages to delete
        request: Bulk delete request with filters, confirmation, and force options
        current_user: Authenticated user (injected by dependency)
        rate_limiter: Rate limiting for bulk operations (injected by dependency)
    
    Returns:
        BulkDeleteResponse: Detailed results including event safety information
        
    Raises:
        HTTPException 400: Invalid request, campaign state, or missing confirmation
        HTTPException 403: User not authorized for this campaign
        HTTPException 404: Campaign not found
        HTTPException 429: Rate limit exceeded
        HTTPException 500: Database or internal server error
    """
    import time
    
    # Check rate limits for bulk operations
    await rate_limiter.check_rate_limit(current_user.id, "bulk_delete_campaign")
    
    start_time = time.time()
    
    try:
        # Use repository context for proper connection management
        from app.db.repositories.campaigns import CampaignRepository
        from app.db.repositories.messages import MessageRepository
        
        # First validate campaign exists and user has access
        async with get_repository_context(CampaignRepository) as campaign_repo:
            campaign = await campaign_repo.get_by_id(campaign_id)
            
            if not campaign:
                raise NotFoundError(message=f"Campaign {campaign_id} not found")
            
            # Check authorization
            if campaign.user_id != current_user.id:
                raise HTTPException(
                    status_code=403, 
                    detail="Not authorized to delete messages from this campaign"
                )
            
            # Safety check - prevent deletion from active campaigns unless force delete
            if campaign.status == "active" and not request.force_delete:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot bulk delete messages from active campaign. Pause the campaign first or use force_delete."
                )
        
        # Perform bulk deletion with event safety
        async with get_repository_context(MessageRepository) as message_repo:
            # Convert datetime objects to ISO strings for repository method
            from_date_str = request.from_date.isoformat() if request.from_date else None
            to_date_str = request.to_date.isoformat() if request.to_date else None
            
            # Execute bulk deletion with enhanced safety
            deleted_count, failed_message_ids, metadata = await message_repo.bulk_delete_campaign_messages(
                campaign_id=campaign_id,
                user_id=current_user.id,
                status=request.status.value if request.status else None,
                from_date=from_date_str,
                to_date=to_date_str,
                limit=request.limit,
                force_delete=request.force_delete,
                batch_size=request.batch_size
            )
            
            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)
            
            # Build applied filters for audit trail
            filters_applied = {}
            if request.status:
                filters_applied["status"] = request.status.value
            if request.from_date:
                filters_applied["from_date"] = from_date_str
            if request.to_date:
                filters_applied["to_date"] = to_date_str
            filters_applied["limit"] = request.limit
            filters_applied["force_delete"] = request.force_delete
            filters_applied["batch_size"] = request.batch_size
            
            # Build response with enhanced metadata
            response = BulkDeleteResponse(
                deleted_count=deleted_count,
                campaign_id=campaign_id,
                failed_count=len(failed_message_ids),
                errors=[f"Failed to delete message: {msg_id}" for msg_id in failed_message_ids],
                operation_type="campaign",
                filters_applied=filters_applied,
                execution_time_ms=execution_time_ms,
                requires_confirmation=metadata.get("requires_confirmation", False),
                events_count=metadata.get("events_count"),
                events_deleted=metadata.get("events_deleted", 0),
                safety_warnings=metadata.get("safety_warnings", []),
                batch_info=metadata.get("batch_info")
            )
            
            # Log successful operation for audit
            logger.info(
                f"User {current_user.id} bulk deleted {deleted_count} messages "
                f"from campaign {campaign_id} in {execution_time_ms}ms "
                f"with filters: {filters_applied}. Events deleted: {metadata.get('events_deleted', 0)}"
            )
            
            return response
            
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log error and return generic error message
        logger.error(f"Error in bulk_delete_campaign_messages: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Error performing bulk deletion: {str(e)}"
        )