"""
Database model for contacts imported from CSV files.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from sqlalchemy import Column, String, DateTime, Boolean, JSON, Integer, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import Base


class Contact(Base):
    """Model for storing contacts imported from CSV files."""
    
    # Import tracking
    import_id = Column(String, ForeignKey("importjob.id"), nullable=False, index=True)
    
    # Contact information
    phone = Column(String, nullable=False, index=True)  # Phone number in E.164 format
    name = Column(String, nullable=True)  # Contact name
    
    # Additional contact data
    tags = Column(JSON, nullable=True, default=list)  # Array of tags for categorization
    
    # CSV row metadata for debugging
    csv_row_number = Column(Integer, nullable=True)  # Original row number in CSV
    raw_data = Column(JSON, nullable=True)  # Store original CSV row data
    
    # Relationships
    import_job = relationship("ImportJob", back_populates="contacts")
    
    # Constraints - ensure unique phone per import
    __table_args__ = (
        UniqueConstraint('import_id', 'phone', name='uix_import_phone'),
    )
    
    # Helper properties
    @property
    def display_name(self) -> str:
        """Get display name, falling back to phone if name is empty."""
        return self.name if self.name else self.phone
    
    @property
    def formatted_phone(self) -> str:
        """Get formatted phone number for display."""
        # Basic formatting - can be enhanced later
        if self.phone.startswith('+1') and len(self.phone) == 12:
            # US number formatting: +1 (555) 123-4567
            return f"+1 ({self.phone[2:5]}) {self.phone[5:8]}-{self.phone[8:]}"
        return self.phone
    
    def add_tag(self, tag: str) -> None:
        """Add a tag to the contact."""
        if self.tags is None:
            self.tags = []
        if tag not in self.tags:
            self.tags.append(tag)
    
    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the contact."""
        if self.tags and tag in self.tags:
            self.tags.remove(tag)