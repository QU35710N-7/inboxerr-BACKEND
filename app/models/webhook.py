"""
Database models for webhook management.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from sqlalchemy import Column, String, DateTime, Boolean, JSON, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Webhook(Base):
    """Webhook configuration model."""
    
    # Webhook configuration
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    event_types = Column(JSON, nullable=False)  # List of event types to send
    is_active = Column(Boolean, default=True, nullable=False)
    secret_key = Column(String, nullable=True)  # For signature validation
    
    # Ownership and association
    user_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    gateway_webhook_id = Column(String, nullable=True)  # ID from SMS gateway
    
    # Stats
    last_triggered_at = Column(DateTime, nullable=True)
    success_count = Column(Integer, default=0, nullable=False)
    failure_count = Column(Integer, default=0, nullable=False)
    
    # Relationships
    user = relationship("User")
    deliveries = relationship("WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan")


class WebhookDelivery(Base):
    """Model for tracking webhook delivery attempts."""
    
    webhook_id = Column(String, ForeignKey("webhook.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    message_id = Column(String, ForeignKey("message.id"), nullable=True, index=True)
    payload = Column(JSON, nullable=False)
    status_code = Column(Integer, nullable=True)
    is_success = Column(Boolean, nullable=False)
    error_message = Column(String, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    next_retry_at = Column(DateTime, nullable=True)
    
    # Relationships
    webhook = relationship("Webhook", back_populates="deliveries")
    message = relationship("Message")


class WebhookEvent(Base):
    """Model for tracking webhook events received from SMS gateway."""
    
    event_type = Column(String, nullable=False, index=True)
    phone_number = Column(String, nullable=True, index=True)
    message_id = Column(String, nullable=True, index=True)
    gateway_message_id = Column(String, nullable=True, index=True)
    payload = Column(JSON, nullable=False)
    processed = Column(Boolean, default=False, nullable=False)
    error_message = Column(String, nullable=True)