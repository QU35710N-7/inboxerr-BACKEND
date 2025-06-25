# app/api/v1/endpoints/campaigns.py
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Path, status
from fastapi.responses import JSONResponse
import logging

from app.api.v1.dependencies import get_current_user, get_rate_limiter
from app.core.exceptions import ValidationError, NotFoundError
from app.schemas.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignResponse,
    CampaignStatus,
)
from app.schemas.user import User
from app.schemas.message import MessageResponse, CampaignBulkDeleteRequest, BulkDeleteResponse
from app.schemas.import_job import ImportJobResponse, ImportStatus
from app.utils.pagination import PaginationParams, paginate_response, PaginatedResponse, PageInfo
from app.services.campaigns.processor import get_campaign_processor
from app.db.session import get_repository_context
from app.utils.ids import generate_prefixed_id, IDPrefix
from app.utils.datetime import utc_now

# Import Repository classes
from app.db.repositories.campaigns import CampaignRepository
from app.db.repositories.messages import MessageRepository  
from app.db.repositories.import_jobs import ImportJobRepository
from app.db.repositories.contacts import ContactRepository
from app.db.repositories.templates import TemplateRepository


router = APIRouter()
logger = logging.getLogger("inboxerr.campaign.endpoint")

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


@router.post("/from-import/{import_job_id}", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign_from_import(
    import_job_id: str,
    campaign: CampaignCreate,
    message_template: str = Query(..., description="Message template for the campaign"),
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
) -> CampaignResponse:
    """
    Create a campaign from an existing successful import job.

    **Phase 2A Workflow:**
    1. Upload CSV: POST /imports/upload (automatic processing)
    2. Monitor Progress: GET /imports/jobs/{job_id}  
    3. Create Campaign: POST /campaigns/from-import/{import_job_id} (this endpoint)
    
    This is the recommended approach for creating campaigns from CSV data.

    """
    # Check rate limits
    await rate_limiter.check_rate_limit(current_user.id, "create_campaign")
    
    try:
        async with get_repository_context(ImportJobRepository) as import_repo:
            # Verify import job exists and is successful
            import_job = await import_repo.get_by_id(import_job_id)
            if not import_job:
                raise NotFoundError(f"Import job {import_job_id} not found")
            
            # Check ownership
            if import_job.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to use this import job"
                )
            
            # Check import job status
            if import_job.status != ImportStatus.SUCCESS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Import job must be in SUCCESS status, currently {import_job.status.value}"
                )
        
        async with get_repository_context(ContactRepository) as contact_repo:
            # Count contacts from import
            contact_count = await contact_repo.get_contacts_count_by_import(import_job_id)
            
            if contact_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Import job has no contacts to create campaign from"
                )
        
        # Step 1: Create template for the campaign
        template = None
        async with get_repository_context(TemplateRepository) as template_repo:
            template = await template_repo.create_template(
                name=f"{campaign.name} - Template",
                content=message_template,
                description=f"Auto-generated template for campaign {campaign.name}",
                user_id=current_user.id,
                is_active=True
            )
        
        # Step 2: Create campaign with template reference (virtual messaging)
        async with get_repository_context(CampaignRepository) as campaign_repo:
            new_campaign = await campaign_repo.create_campaign(
                name=campaign.name,
                description=campaign.description,
                message_content=message_template,
                template_id=template.id,
                total_messages=contact_count,
                user_id=current_user.id,
                scheduled_start_at=campaign.scheduled_start_at,
                scheduled_end_at=campaign.scheduled_end_at,
                settings={
                    **campaign.settings,
                    "import_job_id": import_job_id,
                    "created_from_import": True,
                    "virtual_messaging": True  # Flag for virtual messaging
                }
            )
        
        # No more physical message creation - they'll be generated on-demand during sending
        logger.info(
            f"Created virtual campaign {new_campaign.id} from import job {import_job_id} "
            f"with template {template.id} for {contact_count} contacts (no pre-created messages)"
        )
        
        return new_campaign
        
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating campaign from import: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating campaign from import: {str(e)}"
        )


@router.get("/{campaign_id}/import-status", status_code=status.HTTP_200_OK)
async def get_campaign_import_status(
    campaign_id: str = Path(..., description="Campaign ID"),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Get import status for a campaign created from CSV.
    """
    try:
        async with get_repository_context(CampaignRepository) as campaign_repo:
            # Get campaign
            campaign = await campaign_repo.get_by_id(campaign_id)
            if not campaign:
                raise NotFoundError(f"Campaign {campaign_id} not found")
            
            # Check ownership
            if campaign.user_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access this campaign"
                )
            
            # Check if campaign was created from import
            import_job_id = campaign.settings.get("import_job_id") if campaign.settings else None
            if not import_job_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Campaign was not created from CSV import"
                )
        
        async with get_repository_context(ImportJobRepository) as import_repo:
            # Get import job status
            import_job = await import_repo.get_by_id(import_job_id)
            if not import_job:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Associated import job not found"
                )
            
            return {
                "campaign_id": campaign_id,
                "import_job_id": import_job_id,
                "import_status": import_job.status.value,
                "progress_percentage": import_job.progress_percentage,
                "rows_total": import_job.rows_total,
                "rows_processed": import_job.rows_processed,
                "error_count": import_job.error_count,
                "has_errors": import_job.has_errors,
                "created_from_csv": campaign.settings.get("created_from_csv", False),
                "import_started_at": import_job.started_at.isoformat() if import_job.started_at else None,
                "import_completed_at": import_job.completed_at.isoformat() if import_job.completed_at else None
            }
            
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting campaign import status: {str(e)}"
        )


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
    
async def create_messages_from_contacts(
    session,
    campaign_id: str,
    import_job_id: str,
    message_template: str,
    user_id: Optional[str] = None
) -> int:
    """
    Create messages from imported contacts.
    
    **Phase 2A Pattern:** This function creates campaign messages from contacts
    that were already imported via the /imports/upload pipeline.
    """
    try:
        # Use proper repository classes
        contact_repo = ContactRepository(session)
        message_repo = MessageRepository(session)
        
        # Get all contacts from import
        contacts, _ = await contact_repo.get_by_import_id(import_job_id, limit=10000)
        
        if not contacts:
            logger.warning(f"No contacts found for import job {import_job_id}")
            return 0
        
        # Get user_id from campaign if not provided
        if not user_id:
            from app.models.campaign import Campaign
            from sqlalchemy import select
            campaign_result = await session.execute(
                select(Campaign.user_id).where(Campaign.id == campaign_id)
            )
            user_id = campaign_result.scalar()
        
        # Create messages with personalization
        messages_created = 0
        
        for contact in contacts:
            # Create personalized message using simple template substitution
            personalized_message = message_template
            
            # Variable substitution
            if contact.name:
                personalized_message = personalized_message.replace("{{name}}", contact.name)
                personalized_message = personalized_message.replace("{{contact_name}}", contact.name)
            
            personalized_message = personalized_message.replace("{{phone}}", contact.phone)
            
            # Create message using repository
            await message_repo.create_message(
                phone_number=contact.phone,
                message_text=personalized_message,
                user_id=user_id,
                campaign_id=campaign_id,
                metadata={
                    "contact_name": contact.name,
                    "import_job_id": import_job_id,
                    "contact_tags": contact.tags or []
                }
            )
            messages_created += 1
        
        logger.info(
            f"Created {messages_created} messages from {len(contacts)} contacts "
            f"for campaign {campaign_id}"
        )
        return messages_created
        
    except Exception as e:
        logger.error(f"Error creating messages from contacts: {str(e)}")
        raise