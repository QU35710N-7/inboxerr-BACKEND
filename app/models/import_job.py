"""
Database model for import job tracking.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from enum import Enum

from sqlalchemy import Column, String, DateTime, Boolean, JSON, Integer, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.models.base import Base


class ImportStatus(str, Enum):
    """Import job status enum."""
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportJob(Base):
    """Model for tracking CSV import jobs and their progress."""
    
    # Job identification and status
    status = Column(SQLEnum(ImportStatus), nullable=False, default=ImportStatus.PROCESSING, index=True)
    
    # Progress tracking
    rows_total = Column(Integer, default=0, nullable=False)
    rows_processed = Column(Integer, default=0, nullable=False)
    
    # Error tracking - JSONB with {row, column, message} objects
    errors = Column(JSON, nullable=True, default=list)
    
    # File integrity and metadata
    sha256 = Column(String, nullable=True, index=True)  # SHA-256 hash of uploaded file
    filename = Column(String, nullable=True)  # Original filename
    file_size = Column(Integer, nullable=True)  # File size in bytes
    
    # Processing metadata
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Ownership
    owner_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    
    # Relationships
    owner = relationship("User")
    contacts = relationship("Contact", back_populates="import_job", cascade="all, delete-orphan")
    
    # Helper properties
    @property
    def progress_percentage(self) -> float:
        """Calculate the import progress percentage."""
        if self.rows_total == 0:
            return 0
        return round((self.rows_processed / self.rows_total) * 100, 2)
    
    @property
    def has_errors(self) -> bool:
        """Check if the import has any errors."""
        return self.errors is not None and len(self.errors) > 0
    
    @property
    def error_count(self) -> int:
        """Get the total number of errors."""
        return len(self.errors) if self.errors else 0