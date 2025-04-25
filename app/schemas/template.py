# app/schemas/template.py
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field, validator


class MessageTemplateBase(BaseModel):
    """Base schema for message templates."""
    name: str = Field(..., description="Template name")
    content: str = Field(..., description="Template content with placeholders")
    description: Optional[str] = Field(None, description="Template description")
    is_active: bool = Field(True, description="Whether the template is active")


class MessageTemplateCreate(MessageTemplateBase):
    """Schema for creating a new message template."""
    variables: Optional[List[str]] = Field(default=[], description="List of variables in the template")
    
    @validator("content")
    def validate_content(cls, v):
        """Validate template content."""
        if not v or len(v.strip()) == 0:
            raise ValueError("Template content cannot be empty")
        if len(v) > 1600:  # Max length for multi-part SMS
            raise ValueError("Template exceeds maximum length of 1600 characters")
        return v
    
    @validator("variables", pre=True)
    def validate_variables(cls, v, values):
        """Extract variables from content if not provided."""
        import re
        
        if not v and "content" in values:
            # Extract variables like {{variable_name}} from content
            pattern = r"{{([a-zA-Z0-9_]+)}}"
            matches = re.findall(pattern, values["content"])
            if matches:
                return list(set(matches))  # Return unique variables
        return v or []


class MessageTemplateUpdate(BaseModel):
    """Schema for updating a message template."""
    name: Optional[str] = Field(None, description="Template name")
    content: Optional[str] = Field(None, description="Template content with placeholders")
    description: Optional[str] = Field(None, description="Template description")
    is_active: Optional[bool] = Field(None, description="Whether the template is active")
    variables: Optional[List[str]] = Field(None, description="List of variables in the template")
    
    @validator("content")
    def validate_content(cls, v):
        """Validate template content if provided."""
        if v is not None:
            if len(v.strip()) == 0:
                raise ValueError("Template content cannot be empty")
            if len(v) > 1600:
                raise ValueError("Template exceeds maximum length of 1600 characters")
        return v


class MessageTemplateResponse(MessageTemplateBase):
    """Schema for message template response."""
    id: str = Field(..., description="Template ID")
    variables: List[str] = Field(..., description="List of variables in the template")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    user_id: str = Field(..., description="User who created the template")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class MessageWithTemplate(BaseModel):
    """Schema for sending a message using a template."""
    template_id: str = Field(..., description="Template ID")
    phone_number: str = Field(..., description="Recipient phone number in E.164 format")
    variables: Dict[str, str] = Field(..., description="Values for template variables")
    scheduled_at: Optional[datetime] = Field(None, description="Schedule message for future delivery")
    custom_id: Optional[str] = Field(None, description="Custom ID for tracking")
    
    @validator("phone_number")
    def validate_phone_number(cls, v):
        """Validate phone number format."""
        if not v or not (v.startswith("+") and len(v) >= 8):
            raise ValueError("Phone number must be in E.164 format (e.g. +1234567890)")
        return v