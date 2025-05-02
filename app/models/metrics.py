"""
Database models for metrics.
"""
from datetime import datetime, timezone, date
from typing import Optional, Dict, Any

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import Base


class UserMetrics(Base):
    """Model for storing user-level metrics."""
    
    # Identification and relationships
    user_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    
    # Message metrics
    messages_sent = Column(Integer, default=0, nullable=False)
    messages_delivered = Column(Integer, default=0, nullable=False)
    messages_failed = Column(Integer, default=0, nullable=False)
    messages_scheduled = Column(Integer, default=0, nullable=False)
    
    # Campaign metrics
    campaigns_created = Column(Integer, default=0, nullable=False)
    campaigns_completed = Column(Integer, default=0, nullable=False)
    campaigns_active = Column(Integer, default=0, nullable=False)
    
    # Template metrics
    templates_created = Column(Integer, default=0, nullable=False)
    templates_used = Column(Integer, default=0, nullable=False)
    
    # Usage metrics
    quota_total = Column(Integer, default=1000, nullable=False)
    quota_used = Column(Integer, default=0, nullable=False)
    
    # Additional stats
    meta_data = Column(JSON, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="metrics")
    
    # Constraints
    __table_args__ = (
        UniqueConstraint('user_id', 'date', name='uix_user_date'),
    )