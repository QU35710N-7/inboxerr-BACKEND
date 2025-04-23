"""
Pydantic schemas for user-related API operations.
"""
from typing import List, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, EmailStr, validator


class UserRole(str, Enum):
    """User role enum."""
    ADMIN = "admin"
    USER = "user"
    API = "api"


class UserBase(BaseModel):
    """Base user schema."""
    email: Optional[EmailStr] = Field(None, description="User email address")
    full_name: Optional[str] = Field(None, description="User's full name")
    is_active: Optional[bool] = Field(True, description="Whether the user is active")
    role: Optional[UserRole] = Field(UserRole.USER, description="User role")


class UserCreate(UserBase):
    """Schema for creating a new user."""
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., description="User password")
    
    @validator("password")
    def validate_password(cls, v):
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(char.isdigit() for char in v):
            raise ValueError("Password must contain at least one digit")
        if not any(char.isupper() for char in v):
            raise ValueError("Password must contain at least one uppercase letter")
        return v


class UserUpdate(UserBase):
    """Schema for updating a user."""
    password: Optional[str] = Field(None, description="User password")
    
    @validator("password")
    def validate_password(cls, v):
        """Validate password if provided."""
        if v is not None:
            if len(v) < 8:
                raise ValueError("Password must be at least 8 characters long")
            if not any(char.isdigit() for char in v):
                raise ValueError("Password must contain at least one digit")
            if not any(char.isupper() for char in v):
                raise ValueError("Password must contain at least one uppercase letter")
        return v


class User(UserBase):
    """Schema for user response."""
    id: str = Field(..., description="User ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    
    class Config:
        """Pydantic config."""
        orm_mode = True


class UserInDB(User):
    """Schema for user in database (with hashed password)."""
    hashed_password: str = Field(..., description="Hashed password")


class Token(BaseModel):
    """Schema for authentication token."""
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class TokenData(BaseModel):
    """Schema for token payload."""
    sub: str  # User ID
    exp: Optional[datetime] = None
    role: Optional[str] = None


class APIKey(BaseModel):
    """Schema for API key."""
    id: str = Field(..., description="API key ID")
    key: str = Field(..., description="API key")
    name: str = Field(..., description="API key name")
    user_id: str = Field(..., description="User who owns the API key")
    created_at: datetime = Field(..., description="Creation timestamp")
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")
    is_active: bool = Field(True, description="Whether the API key is active")
    last_used_at: Optional[datetime] = Field(None, description="Last usage timestamp")
    permissions: List[str] = Field(default=[], description="List of permissions")
    
    class Config:
        """Pydantic config."""
        orm_mode = True


class APIKeyCreate(BaseModel):
    """Schema for creating a new API key."""
    name: str = Field(..., description="API key name")
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")
    permissions: Optional[List[str]] = Field(default=[], description="List of permissions")