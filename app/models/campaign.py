# app/models/campaign.py
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, String, DateTime, Boolean, JSON, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Campaign(Base):
    """Campaign model for bulk SMS messaging."""
    
    # Basic campaign information
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    
    # Campaign status
    status = Column(String, nullable=False, default="draft", index=True)  # draft, active, paused, completed, cancelled, failed
    
    # Campaign statistics
    total_messages = Column(Integer, default=0, nullable=False)
    sent_count = Column(Integer, default=0, nullable=False)
    delivered_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    
    # Campaign configuration
    scheduled_start_at = Column(DateTime(timezone=True), nullable=True, index=True)
    scheduled_end_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Campaign settings
    settings = Column(JSON, nullable=True, default=dict)  # Store campaign-specific settings
    
    # Ownership
    user_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    
    # Relationships
    user = relationship("User")
    # In Campaign model:
    messages = relationship("Message", back_populates="campaign", cascade="all, delete-orphan")
    
    # Helper properties
    @property
    def progress_percentage(self) -> float:
        """Calculate the campaign progress percentage."""
        if self.total_messages == 0:
            return 0
        return round((self.sent_count / self.total_messages) * 100, 2)
    
    @property
    def delivery_success_rate(self) -> float:
        """Calculate the delivery success rate."""
        if self.sent_count == 0:
            return 0
        return round((self.delivered_count / self.sent_count) * 100, 2)