# app/api/v1/endpoints/templates.py
"""
API endpoints for message templates.
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import ValidationError, NotFoundError
from app.schemas.template import (
    MessageTemplateCreate,
    MessageTemplateUpdate,
    MessageTemplateResponse,
    MessageWithTemplate
)
from app.schemas.user import User
from app.utils.pagination import PaginationParams, paginate_response
from app.services.sms.sender import get_sms_sender
from app.db.session import get_repository_context

router = APIRouter()


@router.post("/", response_model=MessageTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    template: MessageTemplateCreate,
    current_user: User = Depends(get_current_user)
):
    """
    Create a new message template.
    
    Templates can include variables in the format {{variable_name}} which will
    be replaced when sending messages.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.templates import TemplateRepository
        
        async with get_repository_context(TemplateRepository) as template_repo:
            # Create template
            result = await template_repo.create_template(
                name=template.name,
                content=template.content,
                description=template.description,
                variables=template.variables,
                is_active=template.is_active,
                user_id=current_user.id
            )
            
            return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating template: {str(e)}")


@router.get("/", response_model=Dict[str, Any])
async def list_templates(
    pagination: PaginationParams = Depends(),
    active_only: bool = Query(False, description="Return only active templates"),
    current_user: User = Depends(get_current_user)
):
    """
    List message templates for the current user.
    
    Returns a paginated list of templates.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.templates import TemplateRepository
        
        async with get_repository_context(TemplateRepository) as template_repo:
            # Get templates
            templates, total = await template_repo.get_templates_for_user(
                user_id=current_user.id,
                active_only=active_only,
                skip=pagination.skip,
                limit=pagination.limit
            )
            
            # Return paginated response
            return paginate_response(
                items=[template.dict() for template in templates],
                total=total,
                pagination=pagination
            )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing templates: {str(e)}")


@router.get("/{template_id}", response_model=MessageTemplateResponse)
async def get_template(
    template_id: str = Path(..., description="Template ID"),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific message template.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.templates import TemplateRepository
        
        async with get_repository_context(TemplateRepository) as template_repo:
            # Get template
            template = await template_repo.get_by_id(template_id)
            
            # Check if template exists
            if not template:
                raise NotFoundError(message=f"Template {template_id} not found")
            
            # Check authorization
            if template.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not authorized to access this template")
            
            return template
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting template: {str(e)}")


@router.put("/{template_id}", response_model=MessageTemplateResponse)
async def update_template(
    template_update: MessageTemplateUpdate,
    template_id: str = Path(..., description="Template ID"),
    current_user: User = Depends(get_current_user)
):
    """
    Update a message template.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.templates import TemplateRepository
        
        async with get_repository_context(TemplateRepository) as template_repo:
            # Get template
            template = await template_repo.get_by_id(template_id)
            
            # Check if template exists
            if not template:
                raise NotFoundError(message=f"Template {template_id} not found")
            
            # Check authorization
            if template.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not authorized to update this template")
            
            # Extract variables from content if content was updated
            update_data = template_update.dict(exclude_unset=True)
            if "content" in update_data:
                import re
                pattern = r"{{([a-zA-Z0-9_]+)}}"
                update_data["variables"] = list(set(re.findall(pattern, update_data["content"])))
            
            # Update template
            updated_template = await template_repo.update(id=template_id, obj_in=update_data)
            
            return updated_template
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating template: {str(e)}")


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: str = Path(..., description="Template ID"),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a message template.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.templates import TemplateRepository
        
        async with get_repository_context(TemplateRepository) as template_repo:
            # Get template
            template = await template_repo.get_by_id(template_id)
            
            # Check if template exists
            if not template:
                raise NotFoundError(message=f"Template {template_id} not found")
            
            # Check authorization
            if template.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not authorized to delete this template")
            
            # Delete template
            success = await template_repo.delete(id=template_id)
            
            if not success:
                raise HTTPException(status_code=500, detail="Failed to delete template")
            
            # Return no content
            return None
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting template: {str(e)}")


# Create a Pydantic model for the apply request
class TemplateApplyRequest(BaseModel):
    """Request model for applying a template."""
    template_id: str
    variables: Dict[str, str]


@router.post("/apply")
async def apply_template(
    request: TemplateApplyRequest = Body(...),
    current_user: User = Depends(get_current_user)
):
    """
    Apply variables to a template and return the result.
    
    This endpoint is useful for previewing how a template will look with specific variables.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.templates import TemplateRepository
        
        async with get_repository_context(TemplateRepository) as template_repo:
            # Get template
            template = await template_repo.get_by_id(request.template_id)
            
            # Check if template exists
            if not template:
                raise NotFoundError(message=f"Template {request.template_id} not found")
            
            # Check authorization
            if template.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not authorized to access this template")
            
            # Apply template
            result = await template_repo.apply_template(
                template_id=request.template_id,
                variables=request.variables
            )
            
            # Check for missing variables
            import re
            missing_vars = re.findall(r"{{([a-zA-Z0-9_]+)}}", result)
            
            return {
                "result": result,
                "missing_variables": missing_vars
            }
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error applying template: {str(e)}")


@router.post("/send", status_code=status.HTTP_202_ACCEPTED)
async def send_with_template(
    message: MessageWithTemplate,
    current_user: User = Depends(get_current_user),
    sms_sender = Depends(get_sms_sender)
):
    """
    Send a message using a template.
    
    Applies the provided variables to the template and sends the resulting message.
    """
    try:
        # Use repository context for proper connection management
        from app.db.repositories.templates import TemplateRepository
        
        async with get_repository_context(TemplateRepository) as template_repo:
            # Get template
            template = await template_repo.get_by_id(message.template_id)
            
            # Check if template exists
            if not template:
                raise NotFoundError(message=f"Template {message.template_id} not found")
            
            # Check authorization
            if template.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not authorized to use this template")
            
            # Apply template
            message_text = await template_repo.apply_template(
                template_id=message.template_id,
                variables=message.variables
            )
            
            # Check for missing variables
            import re
            missing_vars = re.findall(r"{{([a-zA-Z0-9_]+)}}", message_text)
            if missing_vars:
                raise ValidationError(
                    message="Missing template variables", 
                    details={"missing_variables": missing_vars}
                )
        
        # Send message using sms_sender which already uses context managers internally
        result = await sms_sender.send_message(
            phone_number=message.phone_number,
            message_text=message_text,
            user_id=current_user.id,
            scheduled_at=message.scheduled_at,
            custom_id=message.custom_id,
            metadata={"template_id": message.template_id, "template_variables": message.variables}
        )
        
        return result
        
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending message: {str(e)}")