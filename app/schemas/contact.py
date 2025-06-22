"""
Pydantic schemas for contact-related API operations.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, validator


class ContactBase(BaseModel):
    """Base schema for contact data."""
    phone: str = Field(..., description="Phone number in E.164 format")
    name: Optional[str] = Field(None, description="Contact name")
    tags: Optional[List[str]] = Field(default=[], description="List of tags for categorization")


class ContactCreate(ContactBase):
    """Schema for creating a new contact."""
    import_id: str = Field(..., description="Import job ID this contact belongs to")
    csv_row_number: Optional[int] = Field(None, description="Original row number in CSV")
    raw_data: Optional[Dict[str, Any]] = Field(None, description="Original CSV row data")
    
    @validator("phone")
    def validate_phone_number(cls, v):
        """Validate phone number format."""
        if not v or not (v.startswith("+") and len(v) >= 8):
            raise ValueError("Phone number must be in E.164 format (e.g. +1234567890)")
        # Additional validation can be added here
        if len(v) > 20:  # Reasonable max length for international numbers
            raise ValueError("Phone number is too long")
        return v
    
    @validator("name")
    def validate_name(cls, v):
        """Validate contact name."""
        if v is not None:
            v = v.strip()
            if len(v) == 0:
                return None  # Convert empty string to None
            if len(v) > 100:
                raise ValueError("Contact name is too long (max 100 characters)")
        return v
    
    @validator("tags")
    def validate_tags(cls, v):
        """Validate tags list."""
        if v is not None:
            # Remove empty tags and duplicates
            v = list(set([tag.strip() for tag in v if tag.strip()]))
            if len(v) > 20:  # Reasonable limit
                raise ValueError("Too many tags (max 20)")
        return v or []


class ContactUpdate(BaseModel):
    """Schema for updating a contact."""
    name: Optional[str] = Field(None, description="Contact name")
    tags: Optional[List[str]] = Field(None, description="List of tags for categorization")
    
    @validator("name")
    def validate_name(cls, v):
        """Validate contact name."""
        if v is not None:
            v = v.strip()
            if len(v) == 0:
                return None
            if len(v) > 100:
                raise ValueError("Contact name is too long (max 100 characters)")
        return v
    
    @validator("tags")
    def validate_tags(cls, v):
        """Validate tags list."""
        if v is not None:
            v = list(set([tag.strip() for tag in v if tag.strip()]))
            if len(v) > 20:
                raise ValueError("Too many tags (max 20)")
        return v


class ContactResponse(ContactBase):
    """Schema for contact response."""
    id: str = Field(..., description="Contact ID")
    import_id: str = Field(..., description="Import job ID this contact belongs to")
    csv_row_number: Optional[int] = Field(None, description="Original row number in CSV")
    raw_data: Optional[Dict[str, Any]] = Field(None, description="Original CSV row data")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    
    # Computed fields
    display_name: str = Field(..., description="Display name (name or phone)")
    formatted_phone: str = Field(..., description="Formatted phone number for display")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class ContactSummary(BaseModel):
    """Schema for contact summary (lightweight response)."""
    id: str = Field(..., description="Contact ID")
    phone: str = Field(..., description="Phone number")
    name: Optional[str] = Field(None, description="Contact name")
    display_name: str = Field(..., description="Display name")
    tags: List[str] = Field(..., description="Contact tags")
    created_at: datetime = Field(..., description="Creation timestamp")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class ContactBulkCreate(BaseModel):
    """Schema for bulk contact creation."""
    import_id: str = Field(..., description="Import job ID")
    contacts: List[ContactCreate] = Field(..., description="List of contacts to create")
    
    @validator("contacts")
    def validate_contacts(cls, v):
        """Validate contacts list."""
        if not v:
            raise ValueError("Contacts list cannot be empty")
        if len(v) > 10000:  # Reasonable batch limit
            raise ValueError("Too many contacts in single batch (max 10,000)")
        
        # Check for duplicate phone numbers within the batch
        phones = [contact.phone for contact in v]
        if len(phones) != len(set(phones)):
            raise ValueError("Duplicate phone numbers found in batch")
        
        return v


class ContactBulkResponse(BaseModel):
    """Schema for bulk contact creation response."""
    import_id: str = Field(..., description="Import job ID")
    total: int = Field(..., description="Total contacts processed")
    created: int = Field(..., description="Number of contacts created")
    skipped: int = Field(..., description="Number of contacts skipped (duplicates)")
    errors: int = Field(..., description="Number of contacts with errors")
    error_details: List[Dict[str, Any]] = Field(default=[], description="Details of any errors")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class ContactSearchFilter(BaseModel):
    """Schema for contact search filters."""
    import_id: Optional[str] = Field(None, description="Filter by import job ID")
    name: Optional[str] = Field(None, description="Search by name (partial match)")
    phone: Optional[str] = Field(None, description="Search by phone number (partial match)")
    tags: Optional[List[str]] = Field(None, description="Filter by tags (OR operation)")
    created_after: Optional[datetime] = Field(None, description="Filter contacts created after this date")
    created_before: Optional[datetime] = Field(None, description="Filter contacts created before this date")
    
    class Config:
        """Pydantic config."""
        schema_extra = {
            "example": {
                "import_id": "import_123",
                "name": "John",
                "tags": ["vip", "customer"],
                "created_after": "2024-01-01T00:00:00Z"
            }
        }