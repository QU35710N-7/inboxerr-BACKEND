"""
Database models for user management.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import Boolean, Column, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship

from app.models.base import Base


class User(Base):
    """User model for authentication and authorization."""
    
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    role = Column(String, default="user", nullable=False)
    
    # Relationships
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")


class APIKey(Base):
    """API key model for API authentication."""
    
    key = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    user_id = Column(String, ForeignKey("user.id"), nullable=False)
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    permissions = Column(JSON, default=list, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="api_keys")