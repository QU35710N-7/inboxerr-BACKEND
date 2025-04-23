# app/api/v1/endpoints/campaigns.py
import csv
import io
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, Path, status
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import get_current_user, get_rate_limiter
from app.core.exceptions import ValidationError, NotFoundError
from app.schemas.campaign import (
    CampaignCreate,
    CampaignCreateFromCSV,
    CampaignUpdate,
    CampaignResponse,
    CampaignStatus,
    CampaignListResponse
)
from app.schemas.user import User
from app.utils.pagination import PaginationParams, paginate_response
from app.services.campaigns.processor import get_campaign_processor

router = APIRouter()


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
        # Get repository
        from app.db.session import get_repository
        from app.db.repositories.campaigns import CampaignRepository
        
        campaign_repo = await get_repository(CampaignRepository)
        
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
        
        # Get repositories
        from app.db.session import get_repository
        from app.db.repositories.campaigns import CampaignRepository
        
        campaign_repo = await get_repository(CampaignRepository)
        
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


@router.get("/", response_model=CampaignListResponse)
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
        # Get repository
        from app.db.session import get_repository
        from app.db.repositories.campaigns import CampaignRepository
        
        campaign_repo = await get_repository(CampaignRepository)
        
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
        return {
            "items": campaigns,
            "total": total,
            "page": pagination.page,
            "size": pagination.limit,
            "pages": total_pages
        }
        
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
       from app.db.session import get_repository
       from app.db.repositories.campaigns import CampaignRepository
       
       campaign_repo = await get_repository(CampaignRepository)
       
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
       from app.db.session import get_repository
       from app.db.repositories.campaigns import CampaignRepository
       
       campaign_repo = await get_repository(CampaignRepository)
       
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
       # Start campaign
       success = await campaign_processor.start_campaign(
           campaign_id=campaign_id,
           user_id=current_user.id
       )
       
       if not success:
           raise HTTPException(status_code=400, detail="Failed to start campaign")
       
       # Get updated campaign
       from app.db.session import get_repository
       from app.db.repositories.campaigns import CampaignRepository
       
       campaign_repo = await get_repository(CampaignRepository)
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
       # Pause campaign
       success = await campaign_processor.pause_campaign(
           campaign_id=campaign_id,
           user_id=current_user.id
       )
       
       if not success:
           raise HTTPException(status_code=400, detail="Failed to pause campaign")
       
       # Get updated campaign
       from app.db.session import get_repository
       from app.db.repositories.campaigns import CampaignRepository
       
       campaign_repo = await get_repository(CampaignRepository)
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
       # Cancel campaign
       success = await campaign_processor.cancel_campaign(
           campaign_id=campaign_id,
           user_id=current_user.id
       )
       
       if not success:
           raise HTTPException(status_code=400, detail="Failed to cancel campaign")
       
       # Get updated campaign
       from app.db.session import get_repository
       from app.db.repositories.campaigns import CampaignRepository
       
       campaign_repo = await get_repository(CampaignRepository)
       campaign = await campaign_repo.get_by_id(campaign_id)
       
       return campaign
       
   except Exception as e:
       raise HTTPException(status_code=500, detail=f"Error cancelling campaign: {str(e)}")


@router.get("/{campaign_id}/messages", response_model=dict)
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
       # First check if campaign exists and belongs to user
       from app.db.session import get_repository
       from app.db.repositories.campaigns import CampaignRepository
       from app.db.repositories.messages import MessageRepository
       
       campaign_repo = await get_repository(CampaignRepository)
       campaign = await campaign_repo.get_by_id(campaign_id)
       
       if not campaign:
           raise NotFoundError(message=f"Campaign {campaign_id} not found")
       
       if campaign.user_id != current_user.id:
           raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
       
       # Get messages for campaign
       message_repo = await get_repository(MessageRepository)
       messages, total = await message_repo.get_messages_for_campaign(
           campaign_id=campaign_id,
           status=status,
           skip=pagination.skip,
           limit=pagination.limit
       )
       
       # Return paginated response
       return paginate_response(
           items=[message.dict() for message in messages],
           total=total,
           pagination=pagination
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
       from app.db.session import get_repository
       from app.db.repositories.campaigns import CampaignRepository
       
       campaign_repo = await get_repository(CampaignRepository)
       
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