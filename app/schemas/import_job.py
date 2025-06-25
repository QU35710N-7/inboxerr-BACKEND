"""
Pydantic schemas for import job-related API operations.
"""
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, validator


class ImportStatus(str, Enum):
    """Import job status enum."""
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportJobBase(BaseModel):
    """Base schema for import job data."""
    filename: Optional[str] = Field(None, description="Original filename")
    file_size: Optional[int] = Field(None, description="File size in bytes")


class ImportJobCreate(ImportJobBase):
    """Schema for creating a new import job."""
    sha256: Optional[str] = Field(None, description="SHA-256 hash of uploaded file")
    
    @validator("file_size")
    def validate_file_size(cls, v):
        """Validate file size limits."""
        if v is not None:
            if v <= 0:
                raise ValueError("File size must be greater than 0")
            if v > 100 * 1024 * 1024:  # 100MB limit
                raise ValueError("File size exceeds 100MB limit")
        return v


class ImportJobUpdate(BaseModel):
    """Schema for updating an import job."""
    status: Optional[ImportStatus] = Field(None, description="Import job status")
    rows_total: Optional[int] = Field(None, description="Total number of rows to process")
    rows_processed: Optional[int] = Field(None, description="Number of rows processed")
    errors: Optional[List[Dict[str, Any]]] = Field(None, description="List of error objects")
    started_at: Optional[datetime] = Field(None, description="Processing start time")
    completed_at: Optional[datetime] = Field(None, description="Processing completion time")
    
    @validator("rows_total", "rows_processed")
    def validate_rows(cls, v):
        """Validate row counts."""
        if v is not None and v < 0:
            raise ValueError("Row count cannot be negative")
        return v


class ImportJobResponse(ImportJobBase):
    """Schema for import job response."""
    id: str = Field(..., description="Import job ID")
    status: ImportStatus = Field(..., description="Import job status")
    rows_total: int = Field(..., description="Total number of rows to process")
    rows_processed: int = Field(..., description="Number of rows processed")
    errors: Optional[List[Dict[str, Any]]] = Field(default=[], description="List of error objects")
    sha256: Optional[str] = Field(None, description="SHA-256 hash of uploaded file")
    started_at: Optional[datetime] = Field(None, description="Processing start time")
    completed_at: Optional[datetime] = Field(None, description="Processing completion time")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    owner_id: str = Field(..., description="User who created the import job")
    
    # Computed fields
    progress_percentage: float = Field(0, description="Import progress percentage")
    has_errors: bool = Field(False, description="Whether the import has errors")
    error_count: int = Field(0, description="Total number of errors")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class ImportJobProgress(BaseModel):
    """Schema for import job progress tracking."""
    id: str = Field(..., description="Import job ID")
    status: ImportStatus = Field(..., description="Current status")
    progress_percentage: float = Field(..., description="Progress percentage (0-100)")
    rows_processed: int = Field(..., description="Number of rows processed")
    rows_total: int = Field(..., description="Total number of rows")
    error_count: int = Field(..., description="Number of errors encountered")
    estimated_completion: Optional[datetime] = Field(None, description="Estimated completion time")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class ImportJobSummary(BaseModel):
    """Schema for import job summary (lightweight response)."""
    id: str = Field(..., description="Import job ID")
    filename: Optional[str] = Field(None, description="Original filename")
    status: ImportStatus = Field(..., description="Import job status")
    progress_percentage: float = Field(..., description="Progress percentage")
    created_at: datetime = Field(..., description="Creation timestamp")
    rows_total: int = Field(..., description="Total rows")
    rows_processed: int = Field(..., description="Processed rows")
    error_count: int = Field(..., description="Error count")
    
    class Config:
        """Pydantic config."""
        from_attributes = True


class ImportError(BaseModel):
    """Schema for individual import errors."""
    row: int = Field(..., description="Row number where error occurred")
    column: Optional[str] = Field(None, description="Column name where error occurred")
    message: str = Field(..., description="Error message")
    value: Optional[str] = Field(None, description="The value that caused the error")
    
    class Config:
        """Pydantic config."""
        schema_extra = {
            "example": {
                "row": 25,
                "column": "phone_number",
                "message": "Invalid phone number format",
                "value": "123-456-7890"
            }
        }


class ColumnInfo(BaseModel):
    """Information about a CSV column for preview."""
    name: str = Field(..., description="Column name from CSV header")
    index: int = Field(..., description="Column index (0-based)")
    sample_values: List[str] = Field(..., description="Sample non-empty values from this column")
    empty_count: int = Field(..., description="Number of empty cells in sample")
    detected_type: str = Field(..., description="Detected data type (phone, name, email, text, number)")
    
class MappingSuggestion(BaseModel):
    """Suggestion for column mapping."""
    column: str = Field(..., description="Column name")
    confidence: float = Field(..., description="Confidence score (0-100)")
    reason: str = Field(..., description="Why this column was suggested")

class ImportPreviewResponse(BaseModel):
    """Response schema for import job preview."""
    job_id: str = Field(..., description="Import job ID")
    file_info: Dict[str, Any] = Field(..., description="File metadata")
    columns: List[ColumnInfo] = Field(..., description="Column information")
    preview_rows: List[Dict[str, str]] = Field(..., description="First 5 rows of data")
    suggestions: Dict[str, List[MappingSuggestion]] = Field(
        ..., 
        description="Mapping suggestions for phone, name, etc."
    )
    confidence_level: Literal["high", "medium", "low"] = Field(
        ..., 
        description="Overall confidence in auto-detection"
    )
    auto_process_recommended: bool = Field(
        ...,
        description="Whether auto-processing is recommended based on confidence"
    )
    messages: List[str] = Field(
        default=[],
        description="User guidance messages"
    )

class ColumnMapping(BaseModel):
    """Schema for explicit column mapping."""
    phone_columns: List[str] = Field(
        ..., 
        description="Column names containing phone numbers",
        min_items=1
    )
    name_column: Optional[str] = Field(
        None,
        description="Column name containing contact names"
    )
    skip_columns: List[str] = Field(
        default=[],
        description="Columns to ignore during import"
    )
    tag_columns: List[str] = Field(
        default=[],
        description="Columns to import as tags"
    )

class ProcessImportRequest(BaseModel):
    """Request schema for processing import with mapping."""
    column_mapping: ColumnMapping = Field(
        ...,
        description="How to map CSV columns to contact fields"
    )
    options: Dict[str, Any] = Field(
        default={
            "skip_invalid_phones": True,
            "merge_duplicate_phones": True,
            "phone_country_default": "US"
        },
        description="Processing options"
    )
    
    class Config:
        """Pydantic config."""
        schema_extra = {
            "example": {
                "column_mapping": {
                    "phone_columns": ["Phone 1", "Phone 2"],
                    "name_column": "First Name",
                    "skip_columns": ["Index"],
                    "tag_columns": ["Company", "Source"]
                },
                "options": {
                    "skip_invalid_phones": True,
                    "phone_country_default": "US"
                }
            }
        }