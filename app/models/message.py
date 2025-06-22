"""
Database models for SMS messages.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from sqlalchemy import Column, String, DateTime, Boolean, JSON, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Message(Base):
    """SMS message model."""
    
    # Core message data
    custom_id = Column(String, unique=True, index=True, nullable=True)
    phone_number = Column(String, nullable=False, index=True)
    message = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)

    # Campaign relationship
    campaign_id = Column(String, ForeignKey("campaign.id"), nullable=True, index=True)
    campaign = relationship("Campaign", back_populates="messages")
    
    # Timestamps for status tracking
    scheduled_at = Column(DateTime(timezone=True), nullable=True, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Additional data
    reason = Column(String, nullable=True)
    gateway_message_id = Column(String, nullable=True, index=True)
    user_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    meta_data = Column(JSON, nullable=True)  # Changed from 'metadata' to 'meta_data' SQLAlchemy reserves metadata

    # Personalization and import tracking
    variables = Column(JSON, nullable=True)  # Store personalization variables as JSONB
    import_id = Column(String, ForeignKey("importjob.id"), nullable=True, index=True)  # Reference to import job
    
    
    # SMS parts tracking
    parts_count = Column(Integer, default=1, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="messages")
    events = relationship("MessageEvent", back_populates="message", cascade="all, delete-orphan")
    batch_id = Column(String, ForeignKey("messagebatch.id"), nullable=True, index=True)
    batch = relationship("MessageBatch", back_populates="messages")
    import_job = relationship("ImportJob")  # NEW: Reference to import job



class MessageEvent(Base):
    """Model for tracking message events and status changes."""
    
    message_id = Column(String, ForeignKey("message.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    data = Column(JSON, nullable=True)
    
    # Relationships
    message = relationship("Message", back_populates="events")


class MessageBatch(Base):
    """Model for tracking message batches."""
    
    name = Column(String, nullable=True)
    total = Column(Integer, default=0, nullable=False)
    processed = Column(Integer, default=0, nullable=False)
    successful = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)
    user_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    messages = relationship("Message", back_populates="batch")
    user = relationship("User")


class MessageTemplate(Base):
    """Model for storing reusable message templates."""
    
    name = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    description = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    user_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    variables = Column(JSON, nullable=True)  # Define expected variables in the template
    
    # Relationships
    user = relationship("User")