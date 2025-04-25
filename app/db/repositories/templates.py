# app/db/repositories/templates.py
"""
Repository for message template operations.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple
from uuid import uuid4
import re

from sqlalchemy import select, update, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.message import MessageTemplate
from app.schemas.template import MessageTemplateCreate, MessageTemplateUpdate


class TemplateRepository(BaseRepository[MessageTemplate, MessageTemplateCreate, MessageTemplateUpdate]):
    """Repository for message template operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize repository with session."""
        super().__init__(session=session, model=MessageTemplate)
    
    async def create_template(
        self,
        *,
        name: str,
        content: str,
        user_id: str,
        description: Optional[str] = None,
        variables: Optional[List[str]] = None,
        is_active: bool = True
    ) -> MessageTemplate:
        """
        Create a new message template.
        
        Args:
            name: Template name
            content: Template content with placeholders
            user_id: User ID
            description: Optional template description
            variables: Optional list of variables
            is_active: Whether the template is active
            
        Returns:
            MessageTemplate: Created template
        """
        # Extract variables from content if not provided
        if variables is None:
            pattern = r"{{([a-zA-Z0-9_]+)}}"
            variables = list(set(re.findall(pattern, content)))
        
        # Create template
        template = MessageTemplate(
            id=str(uuid4()),
            name=name,
            content=content,
            description=description,
            is_active=is_active,
            user_id=user_id,
            variables=variables
        )
        
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        
        return template
    
    async def get_templates_for_user(
        self,
        *,
        user_id: str,
        active_only: bool = False,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[MessageTemplate], int]:
        """
        Get message templates for a user.
        
        Args:
            user_id: User ID
            active_only: Whether to return only active templates
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[MessageTemplate], int]: List of templates and total count
        """
        # Base query
        query = select(MessageTemplate).where(MessageTemplate.user_id == user_id)
        count_query = select(func.count()).select_from(MessageTemplate).where(MessageTemplate.user_id == user_id)
        
        # Filter active templates if requested
        if active_only:
            query = query.where(MessageTemplate.is_active == True)
            count_query = count_query.where(MessageTemplate.is_active == True)
        
        # Order by name
        query = query.order_by(MessageTemplate.name)
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        # Execute queries
        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)
        
        templates = result.scalars().all()
        total = count_result.scalar_one()
        
        return templates, total
    
    async def apply_template(
        self,
        *,
        template_id: str,
        variables: Dict[str, str]
    ) -> Optional[str]:
        """
        Apply variables to a template.
        
        Args:
            template_id: Template ID
            variables: Dictionary of variable values
            
        Returns:
            str: Processed template content or None if template not found
        """
        # Get template
        template = await self.get_by_id(template_id)
        if not template:
            return None
        
        # Apply variables to template
        content = template.content
        
        for key, value in variables.items():
            # Replace {{key}} with value
            content = content.replace(f"{{{{{key}}}}}", value)
        
        return content