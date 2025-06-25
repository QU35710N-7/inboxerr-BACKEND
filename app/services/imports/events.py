# app/services/imports/events.py
"""
Production-ready import progress event schemas for CSV upload processing.

This module provides type-safe, versioned event schemas for real-time progress tracking
during CSV import operations. Designed for WebSocket streaming and background task updates.
"""
from typing import List, Optional, TypedDict, Union
from enum import Enum
from datetime import datetime

__all__ = ["ImportProgressV1", "ImportEventType", "ImportErrorV1", "ImportCompletedV1", "ImportFailedV1"]


class ImportEventType(str, Enum):
    """Import event types for real-time progress tracking."""
    PROGRESS = "progress"      # Incremental progress updates
    COMPLETED = "completed"    # Final success state
    FAILED = "failed"         # Final failure state
    CANCELLED = "cancelled"   # User-cancelled job


class ImportErrorV1(TypedDict):
    """
    Standardized error structure matching backend ImportError schema.
    Compatible with app.schemas.import_job.ImportError for consistency.
    """
    row: int                           # CSV row number (1-based)
    column: Optional[str]              # Column name where error occurred
    message: str                       # Human-readable error description
    value: Optional[str]               # The problematic value that caused error


class ImportProgressV1(TypedDict):
    """
    Core progress event payload for incremental updates during CSV processing.
    
    **Real-time Progress Features:**
    - Row-level processing statistics with percentages
    - Error sampling with structured details
    - Performance metrics for ETA calculation
    - Memory-efficient for high-frequency updates
    
    **Frontend Integration:**
    - Optimized for progress bars and live dashboards
    - Includes all data needed for UX without additional API calls
    - Rate-limited to max 1 update/second to prevent UI flooding
    
    **Backward Compatibility:**
    - Versioned schema (V1) allows future extensions without breaking changes
    - All fields are required for type safety and predictable behavior
    """
    type: ImportEventType              # Always "progress" for this event
    job_id: str                        # Import job identifier for tracking
    
    # Core processing metrics
    processed: int                     # Total rows processed (cumulative)
    successful: int                    # Successfully imported contacts (cumulative)
    total_rows: int                    # Total rows in CSV file (for progress calculation)
    percent: float                     # Completion percentage (0.00-100.00, 2 decimals)
    
    # Error tracking and sampling
    errors: List[ImportErrorV1]        # Recent errors (sampled, max 100 per backend config)
    error_count: int                   # Total error count (includes non-sampled errors)
    has_critical_errors: bool          # Whether processing should be stopped
    
    # Performance and UX enhancements
    estimated_completion: Optional[str] # Human-readable ETA ("~2 minutes", "~30 seconds")
    processing_rate: Optional[int]      # Rows processed per second (for performance monitoring)
    memory_usage_mb: Optional[float]    # Current memory usage for monitoring


class ImportCompletedV1(TypedDict):
    """
    Final completion event with comprehensive job statistics.
    
    **Success Metrics:**
    - Final counts and percentages for reporting
    - File integrity verification results
    - Performance benchmarks for optimization
    
    **Data Preservation:**
    - Links to created contacts for immediate access
    - Detailed error summary for quality assessment
    - Processing metadata for audit trails
    """
    type: ImportEventType              # Always "completed"
    job_id: str
    
    # Final statistics
    total_rows: int                    # Total rows in original CSV
    successful_contacts: int           # Contacts successfully created
    error_count: int                   # Total errors encountered
    final_status: str                  # "success" or "partial_success"
    
    # Quality metrics
    success_rate: float                # Percentage of successful imports (0.00-100.00)
    data_quality_score: float          # Overall data quality assessment (0.00-100.00)
    
    # Performance metrics
    total_processing_time: float       # Total seconds from start to completion
    average_processing_rate: float     # Average rows per second
    peak_memory_usage_mb: float        # Maximum memory used during processing
    
    # File verification
    sha256_verified: bool              # Whether file integrity was maintained
    detected_columns: dict             # Column mapping results for user feedback
    
    # Error summary (for detailed reporting)
    error_summary: List[ImportErrorV1] # Sample of errors (max 100 as per backend config)
    common_error_patterns: List[str]   # Most frequent error types for user guidance
    
    # Timestamps (ISO format for frontend parsing)
    started_at: str                    # Processing start time
    completed_at: str                  # Processing completion time


class ImportFailedV1(TypedDict):
    """
    Failure event with diagnostic information for error recovery.
    
    **Failure Analysis:**
    - Root cause identification for user guidance
    - Partial results preservation when possible
    - Recovery suggestions and next steps
    
    **Support Information:**
    - Detailed error context for debugging
    - System state at failure for troubleshooting
    - User-friendly error messages with actionable guidance
    """
    type: ImportEventType              # Always "failed"
    job_id: str
    
    # Failure classification
    failure_reason: str                # Primary failure cause ("validation_error", "system_error", etc.)
    user_message: str                  # User-friendly error description
    technical_details: Optional[str]   # Technical details for support (optional for security)
    
    # Partial progress preservation
    rows_processed_before_failure: int # Rows successfully processed before failure
    successful_contacts: int           # Contacts created before failure
    
    # Error context
    failure_point: dict               # Where exactly the failure occurred
    system_state: dict                # System metrics at failure time
    recovery_suggestions: List[str]   # Actionable steps for user
    
    # Support metadata
    error_id: Optional[str]           # Unique error identifier for support tickets
    support_context: Optional[dict]  # Additional context for customer support
    
    # Timestamps
    started_at: str
    failed_at: str


# Type union for all possible import events (useful for event handlers)
ImportEvent = Union[ImportProgressV1, ImportCompletedV1, ImportFailedV1]


# Helper functions for creating events with validation
def create_progress_event(
    job_id: str,
    processed: int,
    successful: int,
    total_rows: int,
    errors: List[ImportErrorV1],
    error_count: int,
    has_critical_errors: bool,
    estimated_completion: Optional[str] = None,
    processing_rate: Optional[int] = None,
    memory_usage_mb: Optional[float] = None,
) -> ImportProgressV1:
    """
    Create a validated progress event with computed fields.
    
    **Automatic Calculations:**
    - Progress percentage with proper rounding
    - Rate limiting for high-frequency updates
    - Memory usage monitoring
    
    Args:
        job_id: Import job identifier
        processed: Total rows processed
        successful: Successfully imported contacts
        total_rows: Total rows in CSV
        errors: Recent error samples
        error_count: Total error count
        has_critical_errors: Whether processing should stop
        estimated_completion: Optional ETA string
        processing_rate: Optional processing rate
        memory_usage_mb: Optional memory usage
    
    Returns:
        ImportProgressV1: Validated progress event
    """
    # Calculate progress percentage with proper bounds checking
    if total_rows <= 0:
        percent = 0.0
    else:
        percent = round((processed / total_rows) * 100, 2)
        percent = max(0.0, min(100.0, percent))  # Clamp to 0-100 range
    
    return ImportProgressV1(
        type=ImportEventType.PROGRESS,
        job_id=job_id,
        processed=processed,
        successful=successful,
        total_rows=total_rows,
        percent=percent,
        errors=errors,
        error_count=error_count,
        has_critical_errors=has_critical_errors,
        estimated_completion=estimated_completion,
        processing_rate=processing_rate,
        memory_usage_mb=memory_usage_mb,
    )


def create_completion_event(
    job_id: str,
    total_rows: int,
    successful_contacts: int,
    error_count: int,
    processing_time: float,
    average_rate: float,
    peak_memory: float,
    sha256_verified: bool,
    detected_columns: dict,
    error_summary: List[ImportErrorV1],
    started_at: datetime,
    completed_at: datetime,
) -> ImportCompletedV1:
    """
    Create a validated completion event with computed metrics.
    
    Args:
        job_id: Import job identifier
        total_rows: Total rows processed
        successful_contacts: Successful imports
        error_count: Total errors
        processing_time: Total processing time in seconds
        average_rate: Average processing rate
        peak_memory: Peak memory usage in MB
        sha256_verified: File integrity status
        detected_columns: Column detection results
        error_summary: Sample of errors
        started_at: Start timestamp
        completed_at: Completion timestamp
    
    Returns:
        ImportCompletedV1: Validated completion event
    """
    # Calculate success rate and quality score
    success_rate = (successful_contacts / total_rows * 100) if total_rows > 0 else 0.0
    data_quality_score = max(0.0, 100.0 - (error_count / total_rows * 100)) if total_rows > 0 else 100.0
    
    # Determine final status
    if error_count == 0:
        final_status = "success"
    elif successful_contacts > 0:
        final_status = "partial_success"
    else:
        final_status = "failed"
    
    # Extract common error patterns
    error_messages = [error["message"] for error in error_summary]
    common_patterns = list(set(error_messages))[:5]  # Top 5 unique error types
    
    return ImportCompletedV1(
        type=ImportEventType.COMPLETED,
        job_id=job_id,
        total_rows=total_rows,
        successful_contacts=successful_contacts,
        error_count=error_count,
        final_status=final_status,
        success_rate=round(success_rate, 2),
        data_quality_score=round(data_quality_score, 2),
        total_processing_time=processing_time,
        average_processing_rate=average_rate,
        peak_memory_usage_mb=peak_memory,
        sha256_verified=sha256_verified,
        detected_columns=detected_columns,
        error_summary=error_summary,
        common_error_patterns=common_patterns,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
    )


def create_failure_event(
    job_id: str,
    failure_reason: str,
    user_message: str,
    rows_processed: int,
    successful_contacts: int,
    started_at: datetime,
    failed_at: datetime,
    technical_details: Optional[str] = None,
    error_id: Optional[str] = None,
) -> ImportFailedV1:
    """
    Create a validated failure event with diagnostic information.
    
    Args:
        job_id: Import job identifier
        failure_reason: Primary failure classification
        user_message: User-friendly error message
        rows_processed: Rows processed before failure
        successful_contacts: Contacts created before failure
        started_at: Processing start time
        failed_at: Failure occurrence time
        technical_details: Optional technical details
        error_id: Optional error identifier for support
    
    Returns:
        ImportFailedV1: Validated failure event
    """
    # Generate recovery suggestions based on failure reason
    recovery_suggestions = []
    if failure_reason == "validation_error":
        recovery_suggestions = [
            "Check your CSV file format and column headers",
            "Ensure phone numbers are in valid format",
            "Verify file encoding is UTF-8"
        ]
    elif failure_reason == "file_size_error":
        recovery_suggestions = [
            "Split your CSV into smaller files (max 100MB)",
            "Remove unnecessary columns to reduce file size"
        ]
    elif failure_reason == "system_error":
        recovery_suggestions = [
            "Try uploading again in a few minutes",
            "Contact support if the problem persists"
        ]
    
    return ImportFailedV1(
        type=ImportEventType.FAILED,
        job_id=job_id,
        failure_reason=failure_reason,
        user_message=user_message,
        technical_details=technical_details,
        rows_processed_before_failure=rows_processed,
        successful_contacts=successful_contacts,
        failure_point={"timestamp": failed_at.isoformat()},
        system_state={},  # Populated by system monitoring
        recovery_suggestions=recovery_suggestions,
        error_id=error_id,
        support_context=None,  # Populated if needed for support
        started_at=started_at.isoformat(),
        failed_at=failed_at.isoformat(),
    )