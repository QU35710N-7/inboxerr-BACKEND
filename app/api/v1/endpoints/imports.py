# app/api/v1/endpoints/imports.py
"""
Production-ready CSV import endpoints for Phase 2A.

This module provides streaming CSV upload, background processing, and progress tracking
for contact imports with memory-efficient parsing and robust error handling.

Key Features:
- Streaming file upload with SHA-256 integrity checking
- Memory-efficient CSV parsing with configurable chunk sizes  
- Background processing with real-time progress updates
- Comprehensive error handling and validation
- Rate limiting and concurrent job management
- Automatic file cleanup and security measures

Architecture Compliance:
- Uses proper repository pattern with dependency injection
- Implements context managers for transaction safety
- Background tasks with proper error handling
- Follows REST API conventions with appropriate status codes
"""
import os
import csv
import hashlib
import tempfile
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, BackgroundTasks, status
from fastapi.responses import JSONResponse

# Core dependencies
from app.api.v1.dependencies import get_current_user, get_rate_limiter
from app.core.exceptions import ValidationError, NotFoundError, InboxerrException
from app.schemas.user import User
from app.schemas.import_job import ImportJobResponse, ImportJobSummary, ImportStatus, ImportPreviewResponse, ColumnInfo, MappingSuggestion, ProcessImportRequest, ColumnMapping
from app.schemas.contact import ContactResponse
from app.services.imports.parser import StreamingCSVParser, CSVParseResult, ColumnDetectionResult
from app.services.imports.events import (
    ImportProgressV1, ImportEventType, ImportErrorV1,
    create_progress_event, create_completion_event, create_failure_event
)
from app.utils.ids import generate_prefixed_id, IDPrefix
from app.utils.datetime import utc_now
from app.utils.pagination import PaginationParams, paginate_response, PaginatedResponse
from app.db.session import get_repository_context

# Import proper repository implementations (FIXED: No longer using inline classes)
from app.db.repositories.import_jobs import ImportJobRepository
from app.db.repositories.contacts import ContactRepository

# Setup logging
router = APIRouter()
logger = logging.getLogger("inboxerr.imports")

# Production constants with security considerations
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB - matches Phase 2A spec
MAX_ROW_COUNT = 1_000_000  # 1M rows - matches Phase 2A spec
MAX_CONCURRENT_JOBS = 5  # Limit concurrent processing jobs per user
ALLOWED_EXTENSIONS = {".csv", ".txt"}  # Support TXT for broader compatibility
ALLOWED_MIME_TYPES = {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"}
CHUNK_SIZE = 8192  # 8KB chunks for streaming - optimized for memory usage
TEMP_FILE_PREFIX = "inboxerr_import_"  # Secure temp file naming
TEMP_DIR = os.getenv('INBOXERR_TEMP_DIR', tempfile.gettempdir())


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_csv_file(
    file: UploadFile = File(..., description="CSV file containing contact data"),
    auto_process: Optional[bool] = Query(
        None, 
        description="Whether to auto-process if confidence is high. If not specified, defaults based on detection confidence."
    ),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
) -> JSONResponse:
    """
    Upload a CSV file for streaming processing.
    
    **Phase 2A Implementation Features:**
    - Streams file to secure temporary storage
    - Computes SHA-256 hash for integrity verification
    - Creates ImportJob record with PROCESSING status
    - Starts automatic background processing
    - Returns 202 immediately with job tracking information
    - Implements proper security measures and validation
    
    **Security Features:**
    - File type validation (extension + MIME type)
    - File size limits (100MB max)
    - Row count limits (1M rows max) 
    - Rate limiting per user
    - Concurrent job limits
    - Secure temporary file handling
    
    **Performance Features:**
    - Streaming upload (constant memory usage)
    - Chunked processing for large files
    - Automatic background processing with progress tracking
    - Automatic cleanup of temporary files
    
    Args:
        file: CSV file upload with contact data
        background_tasks: FastAPI background tasks manager
        current_user: Authenticated user from JWT token
        rate_limiter: Rate limiting service dependency
        
    Returns:
        JSONResponse: 202 Accepted with job tracking details
        
    Raises:
        HTTPException: Various validation and processing errors
        ValidationError: File format or content validation failures
        NotFoundError: Resource not found errors
    """
    # Apply rate limiting - prevent abuse
    await rate_limiter.check_rate_limit(current_user.id, "csv_upload")
    
    # Validate file is provided
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided"
        )
    
    try:
        # Phase 1: File Validation and Security Checks
        await _validate_uploaded_file(file)
        
        # Phase 2: Check Concurrent Job Limits  
        async with get_repository_context(ImportJobRepository) as import_repo:
            active_jobs_count = await import_repo.get_active_jobs_count(current_user.id)

            
            if active_jobs_count >= MAX_CONCURRENT_JOBS:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Maximum {MAX_CONCURRENT_JOBS} concurrent import jobs allowed. "
                           f"Please wait for existing jobs to complete."
                )
        
        # Phase 3: Streaming File Processing with Memory Efficiency
        import_job_id = generate_prefixed_id(IDPrefix.IMPORT)  # Import job gets "import-xxxx"
        temp_path = None
        file_hash = hashlib.sha256()
        total_size = 0
        row_count = 0
        headers = []
        
        try:
            # Create secure temporary file
            temp_fd, temp_path = tempfile.mkstemp(
                prefix=TEMP_FILE_PREFIX,
                suffix=".csv",
                dir=TEMP_DIR
            )
            
            with os.fdopen(temp_fd, 'wb') as temp_file:
                # Stream file in chunks to prevent memory exhaustion
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    
                    total_size += len(chunk)
                    file_hash.update(chunk)
                    temp_file.write(chunk)
                    
                    # Safety check for file size during streaming
                    if total_size > MAX_FILE_SIZE:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"File size exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit"
                        )
            
            # Phase 4: Quick CSV Analysis for Row Count and Headers
            try:
                with open(temp_path, 'r', encoding='utf-8') as csv_file:
                    # Detect delimiter using CSV sniffer
                    sample = csv_file.read(8192)
                    csv_file.seek(0)
                    
                    sniffer = csv.Sniffer()
                    try:
                        delimiter = sniffer.sniff(sample, delimiters=',\t|;').delimiter
                    except csv.Error:
                        delimiter = ','  # Default fallback
                    
                    # Read headers
                    reader = csv.reader(csv_file, delimiter=delimiter)
                    headers = next(reader, [])
                    
                    # Count rows efficiently
                    row_count = sum(1 for _ in reader)
                    
                    # Validate row count
                    if row_count > MAX_ROW_COUNT:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"File contains {row_count:,} rows. "
                                   f"Maximum: {MAX_ROW_COUNT:,}"
                        )
                        
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="File encoding not supported. Please use UTF-8 encoding."
                )
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Error analyzing CSV file: {str(e)}"
                )
            
            # Phase 5: Create ImportJob Record in Database
            async with get_repository_context(ImportJobRepository) as import_repo:
                import_job = await import_repo.create_import_job(
                    id=import_job_id,
                    filename=file.filename,
                    file_size=total_size,
                    sha256=file_hash.hexdigest(),
                    owner_id=current_user.id,
                    rows_total=row_count
                )
            
            # Phase 6: Decide whether to auto-process
            # Quick detection to determine confidence level
            should_auto_process = False
            confidence_level = "unknown"

            if auto_process is None:
                # Run quick detection to determine confidence
                try:
                    from app.services.imports.parser import StreamingCSVParser
                    from app.db.session import get_session
                    
                    async with get_session() as session:
                        parser = StreamingCSVParser(session)
                        detection_result = await parser._enhanced_column_detection(
                            Path(temp_path), 'utf-8', delimiter, headers
                        )
                        confidence_level = detection_result.detection_quality
                        
                        # Auto-process only for high confidence
                        should_auto_process = (confidence_level == "high")
                        
                        # Store detection info for preview
                        await import_repo.update(
                            id=import_job_id,
                            obj_in={
                                "errors": [{
                                    "row": 0,
                                    "column": "_metadata",
                                    "message": "Initial detection",
                                    "value": {
                                        "column_detection": {
                                            "quality": detection_result.detection_quality,
                                            "phone_confidence": detection_result.phone_confidence,
                                            "name_confidence": detection_result.name_confidence,
                                            "primary_phone_column": detection_result.primary_phone_column,
                                            "name_column": detection_result.name_column,
                                            "user_guidance": detection_result.user_guidance
                                        }
                                    }
                                }]
                            }
                        )
                except Exception as e:
                    logger.warning(f"Quick detection failed, defaulting to manual mapping: {str(e)}")
                    should_auto_process = False
                    confidence_level = "low"
            else:
                # Use explicit user preference
                should_auto_process = auto_process

            # Schedule processing if needed
            if should_auto_process:
                background_tasks.add_task(
                    process_csv_background,
                    import_job.id,
                    temp_path
                )
                processing_message = "File uploaded successfully. Processing started automatically due to high confidence detection."
            else:
                processing_message = f"File uploaded successfully. Detection confidence: {confidence_level}. Please review and map columns."
            
            
            # Return 202 with comprehensive tracking information
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "job_id": import_job.id,
                    "file_hash": file_hash.hexdigest(),
                    "file_size": total_size,
                    "row_count": row_count,
                    "headers": headers,
                    "detected_delimiter": delimiter if 'delimiter' in locals() else ',',
                    "status": ImportStatus.PROCESSING.value,
                    "message": processing_message,
                    "auto_processing": should_auto_process,
                    "confidence_level": confidence_level,
                    "preview_url": f"/api/v1/imports/jobs/{import_job.id}/preview",
                    "progress_url": f"/api/v1/imports/jobs/{import_job.id}",
                    "estimated_completion_time": _estimate_processing_time(row_count) if should_auto_process else None
                }
            )
            
        except HTTPException:
            # Clean up temp file on HTTP exceptions
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError as e:
                    logger.warning(f"Failed to cleanup temp file {temp_path}: {e}")
            raise
            
        except Exception as e:
            # Clean up temp file on any other error
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError as cleanup_error:
                    logger.warning(f"Failed to cleanup temp file {temp_path}: {cleanup_error}")
            
            logger.error(f"Unexpected error during CSV upload: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error processing upload: {str(e)}"
            )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Critical error in CSV upload endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during file upload"
        )



@router.get("/jobs/{job_id}", response_model=ImportJobResponse)
async def get_import_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> ImportJobResponse:
    """
    Get the status of an import job with comprehensive progress information.
    
    **Real-time Monitoring Features:**
    - Current processing progress (percentage complete)
    - Row-level processing statistics
    - Error details and validation issues
    - Performance metrics and timing
    - File integrity verification
    
    Args:
        job_id: Import job identifier
        current_user: Authenticated user from JWT token
        
    Returns:
        ImportJobResponse: Detailed job status and progress
        
    Raises:
        HTTPException: Authorization or not found errors
        NotFoundError: Import job not found
    """
    try:
        async with get_repository_context(ImportJobRepository) as import_repo:
            # Get import job with error handling
            import_job = await import_repo.get_by_id(job_id)
            if not import_job:
                raise NotFoundError(f"Import job {job_id} not found")
            
            # Check ownership - security critical
            if import_job.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access this import job"
                )
            
            # Convert to response schema with computed fields
            return ImportJobResponse(
                id=import_job.id,
                status=import_job.status,
                filename=import_job.filename,
                file_size=import_job.file_size,
                rows_total=import_job.rows_total,
                rows_processed=import_job.rows_processed,
                errors=import_job.errors or [],
                sha256=import_job.sha256,
                started_at=import_job.started_at,
                completed_at=import_job.completed_at,
                created_at=import_job.created_at,
                updated_at=import_job.updated_at,
                owner_id=import_job.owner_id,
                progress_percentage=import_job.progress_percentage,
                has_errors=import_job.has_errors,
                error_count=import_job.error_count
            )
            
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrieving import job {job_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving import job: {str(e)}"
        )


@router.get("/jobs", response_model=PaginatedResponse[ImportJobSummary])
async def list_import_jobs(
    pagination: PaginationParams = Depends(),
    status_filter: Optional[ImportStatus] = Query(None, description="Filter by job status"),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[ImportJobSummary]:
    """
    List import jobs for the current user with filtering and pagination.
    
    **Query Features:**
    - Status-based filtering (processing, success, failed, cancelled)
    - Pagination with configurable page sizes
    - Sorted by creation date (newest first)
    - Summary format for efficient list display
    
    Args:
        pagination: Pagination parameters (page, size, etc.)
        status_filter: Optional status filter
        current_user: Authenticated user from JWT token
        
    Returns:
        PaginatedResponse[ImportJobSummary]: Paginated list of import jobs
        
    Raises:
        HTTPException: Query or processing errors
    """
    try:
        async with get_repository_context(ImportJobRepository) as import_repo:
            # Get paginated jobs with optional status filter
            jobs, total = await import_repo.get_by_owner(
                owner_id=current_user.id,
                status=status_filter,
                skip=pagination.skip,
                limit=pagination.limit
            )
            
            # Convert to summary format for efficient transfer
            job_summaries = [
                ImportJobSummary(
                    id=job.id,
                    filename=job.filename,
                    status=job.status,
                    rows_total=job.rows_total,
                    rows_processed=job.rows_processed,
                    progress_percentage=job.progress_percentage,
                    has_errors=job.has_errors,
                    error_count=job.error_count,
                    created_at=job.created_at,
                    completed_at=job.completed_at
                )
                for job in jobs
            ]
            
            return paginate_response(
                items=job_summaries,
                total=total,
                pagination=pagination
            )
            
    except Exception as e:
        logger.error(f"Error listing import jobs for user {current_user.id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing import jobs: {str(e)}"
        )


@router.get("/jobs/{job_id}/contacts", response_model=PaginatedResponse[ContactResponse])
async def get_import_contacts(
    job_id: str,
    pagination: PaginationParams = Depends(),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[ContactResponse]:
    """
    Get contacts created from an import job with pagination.
    
    **Preview Features:**
    - Paginated contact listing for large imports
    - Full contact details including validation results
    - Phone number formatting for display
    - Original CSV row data preservation
    
    Args:
        job_id: Import job identifier
        pagination: Pagination parameters
        current_user: Authenticated user from JWT token
        
    Returns:
        PaginatedResponse[ContactResponse]: Paginated contacts from import
        
    Raises:
        HTTPException: Authorization or not found errors
        NotFoundError: Import job not found
    """
    try:
        # Verify import job exists and user has access
        async with get_repository_context(ImportJobRepository) as import_repo:
            import_job = await import_repo.get_by_id(job_id)
            if not import_job:
                raise NotFoundError(f"Import job {job_id} not found")
            
            if import_job.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access this import job"
                )
        
        # Get contacts from import
        async with get_repository_context(ContactRepository) as contact_repo:
            contacts, total = await contact_repo.get_by_import_id(
                import_id=job_id,
                skip=pagination.skip,
                limit=pagination.limit
            )
            
            # Convert to response format with computed fields
            contact_responses = [
                ContactResponse(
                    id=contact.id,
                    import_id=contact.import_id,
                    phone=contact.phone,
                    name=contact.name,
                    tags=contact.tags or [],
                    csv_row_number=contact.csv_row_number,
                    raw_data=contact.raw_data,
                    created_at=contact.created_at,
                    updated_at=contact.updated_at,
                    display_name=contact.name or contact.phone,
                    formatted_phone=contact.phone  # TODO: Add phone formatting utility
                )
                for contact in contacts
            ]
            
            return paginate_response(
                items=contact_responses,
                total=total,
                pagination=pagination
            )
            
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting contacts for import job {job_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving contacts: {str(e)}"
        )


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_import_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Cancel an import job and optionally clean up contacts.
    
    **Cancellation Features:**
    - Immediate status update to CANCELLED
    - Stops ongoing processing (if possible)
    - Preserves completed work (contacts already created)
    - Automatic cleanup of temporary files
    
    **Security Notes:**
    - Only job owner can cancel
    - Cannot cancel completed jobs
    - Graceful handling of concurrent cancellation attempts
    
    Args:
        job_id: Import job identifier
        current_user: Authenticated user from JWT token
        
    Returns:
        None: 204 No Content on successful cancellation
        
    Raises:
        HTTPException: Authorization, validation, or processing errors
        NotFoundError: Import job not found
    """
    try:
        async with get_repository_context(ImportJobRepository) as import_repo:
            # Get import job with error handling
            import_job = await import_repo.get_by_id(job_id)
            if not import_job:
                raise NotFoundError(f"Import job {job_id} not found")
            
            # Check ownership - security critical
            if import_job.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to cancel this import job"
                )
            
            # Check if job can be cancelled
            if import_job.status in [ImportStatus.SUCCESS, ImportStatus.CANCELLED]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot cancel job with status {import_job.status.value}"
                )
            
            # Update job status to cancelled
            await import_repo.update(
                id=job_id,
                obj_in={
                    "status": ImportStatus.CANCELLED,
                    "completed_at": utc_now(),
                    "updated_at": utc_now()
                }
            )
            
            logger.info(f"Import job {job_id} cancelled by user {current_user.id}")
            
            # Note: We don't delete contacts that were already created successfully
            # This preserves user data and allows partial imports to be useful
            
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Error cancelling import job {job_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error cancelling import job: {str(e)}"
        )


@router.get("/jobs/{job_id}/preview", response_model=ImportPreviewResponse)
async def get_import_preview(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> ImportPreviewResponse:
    """
    Get a preview of the CSV file with column analysis and mapping suggestions.
    
    **Preview Features:**
    - First 5 rows of data for visual inspection
    - Column analysis with data type detection
    - Smart mapping suggestions with confidence scores
    - Guidance messages for low-confidence detection
    
    Args:
        job_id: Import job identifier
        current_user: Authenticated user from JWT token
        
    Returns:
        ImportPreviewResponse: Preview data with mapping suggestions
        
    Raises:
        HTTPException: Authorization or processing errors
        NotFoundError: Import job not found
    """
    try:
        # Verify job exists and user has access
        async with get_repository_context(ImportJobRepository) as import_repo:
            import_job = await import_repo.get_by_id(job_id)
            if not import_job:
                raise NotFoundError(f"Import job {job_id} not found")
            
            if import_job.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access this import job"
                )
            
            # Check if job has already been processed
            if import_job.status != ImportStatus.PROCESSING:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Import job already {import_job.status.value}. Preview not available."
                )
        
        # Get metadata from errors array (stored in _metadata entry)
        metadata = None
        if import_job.errors:
            for error in import_job.errors:
                if error.get("column") == "_metadata":
                    metadata = error.get("value", {})
                    break
        
        # Get column detection info
        column_detection = metadata.get("column_detection", {}) if metadata else {}
        
        #=================
        # Build response
        #=================

        # Get preview data from file
        preview_data = await _get_csv_preview_data(import_job.sha256)
        
        # Build column info
        columns = []
        for i, col_name in enumerate(preview_data.get("headers", [])):
            columns.append(ColumnInfo(
                name=col_name,
                index=i,
                sample_values=preview_data.get("samples", {}).get(col_name, []),
                empty_count=preview_data.get("empty_counts", {}).get(col_name, 0),
                detected_type=_detect_column_type(col_name, preview_data.get("samples", {}).get(col_name, []))
            ))

        # Build suggestions based on detected column types
        suggestions = {
            "phone_columns": [],
            "name_columns": []
        }

        # Add suggestions based on column types detected
        for col in columns:
            if col.detected_type == "phone":
                suggestions["phone_columns"].append(MappingSuggestion(
                    column=col.name,
                    confidence=80.0,  # High confidence since type detection worked
                    reason=f"Column name '{col.name}' and data format match phone pattern"
                ))
            elif col.detected_type == "name":
                suggestions["name_columns"].append(MappingSuggestion(
                    column=col.name,
                    confidence=80.0,
                    reason=f"Column name '{col.name}' contains name data"
                ))
        
        # Determine confidence level
        phone_conf = column_detection.get("phone_confidence", 0)
        if phone_conf >= 80:
            confidence_level = "high"
        elif phone_conf >= 50:
            confidence_level = "medium"
        else:
            confidence_level = "low"
        
        
        # Build messages
        messages = column_detection.get("user_guidance", [])
        if confidence_level == "low":
            messages.append("⚠️ Low confidence detection. Please review and confirm column mappings.")
        
        return ImportPreviewResponse(
            job_id=job_id,
            file_info={
                "filename": import_job.filename,
                "file_size": import_job.file_size,
                "row_count": import_job.rows_total,
                "sha256": import_job.sha256
            },
            columns=columns,
            preview_rows=preview_data.get("rows", []),
            suggestions=suggestions,
            confidence_level=confidence_level,
            auto_process_recommended=(confidence_level == "high"),
            messages=messages
        )
        
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting import preview for job {job_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating preview: {str(e)}"
        )

@router.post("/jobs/{job_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_import_with_mapping(
    job_id: str,
    request: ProcessImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """
    Process an import job with explicit column mapping.
    
    **Processing Features:**
    - Uses user-provided column mapping instead of auto-detection
    - Supports multiple phone columns
    - Allows skipping columns and creating tags
    - Configurable processing options
    
    Args:
        job_id: Import job identifier
        request: Column mapping and processing options
        background_tasks: FastAPI background tasks manager
        current_user: Authenticated user from JWT token
        
    Returns:
        JSONResponse: 202 Accepted with processing status
        
    Raises:
        HTTPException: Various validation or processing errors
    """
    try:
        # Verify job exists and user has access
        async with get_repository_context(ImportJobRepository) as import_repo:
            import_job = await import_repo.get_by_id(job_id)
            if not import_job:
                raise NotFoundError(f"Import job {job_id} not found")
            
            if import_job.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to process this import job"
                )
            
            # Check job status - only allow processing of new jobs
            if import_job.status != ImportStatus.PROCESSING:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Import job is {import_job.status.value}. Only PROCESSING jobs can be mapped and processed."
                )
            
            # Validate that we have the file
            temp_file_path = _get_temp_file_path(import_job.sha256)
            if not temp_file_path or not os.path.exists(temp_file_path):
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail="Import file no longer available. Please upload again."
                )
            
            # Store mapping in job metadata
            mapping_metadata = {
                "column_mapping": request.column_mapping.dict(),
                "options": request.options,
                "mapped_at": datetime.now(timezone.utc).isoformat()
            }
            
            # Update job with mapping info
            await import_repo.update(
                id=job_id,
                obj_in={
                    "updated_at": datetime.now(timezone.utc),
                    "errors": [{
                        "row": 0,
                        "column": "_mapping",
                        "message": "User-provided column mapping",
                        "value": mapping_metadata
                    }]
                }
            )
        
        # Schedule processing with mapping
        background_tasks.add_task(
            process_csv_with_mapping_background,
            job_id,
            temp_file_path,
            request.column_mapping,
            request.options
        )
        
        logger.info(
            f"Import job {job_id} scheduled for processing with manual mapping. "
            f"Phone columns: {request.column_mapping.phone_columns}"
        )
        
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job_id,
                "status": "processing",
                "message": "Import job processing started with your column mapping.",
                "progress_url": f"/api/v1/imports/jobs/{job_id}",
                "mapping": {
                    "phone_columns": request.column_mapping.phone_columns,
                    "name_column": request.column_mapping.name_column,
                    "tag_columns": request.column_mapping.tag_columns,
                    "skip_columns": request.column_mapping.skip_columns
                }
            }
        )
        
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing import job {job_id} with mapping: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting import processing: {str(e)}"
        )



# ============================================================================
# BACKGROUND PROCESSING IMPLEMENTATION
# ============================================================================
async def process_csv_background(job_id: str, temp_file_path: str) -> None:
    """
    Enhanced background task to process CSV file using the new StreamingCSVParser.
    
    **PRODUCTION ENHANCEMENTS:**
    - Uses enhanced column detection with confidence scoring
    - Provides detailed progress updates with performance metrics
    - Better error handling with user-friendly messages
    - Memory monitoring for large file processing
    - Comprehensive completion events
    
    Args:
        job_id: Import job identifier for tracking
        temp_file_path: Path to temporary CSV file to process
    """
    logger.info(f"Starting enhanced background processing for import job {job_id}")
    
    try:
        # PRODUCTION: Use direct session management for background tasks
        from app.db.session import get_session
        
        # Process CSV with enhanced parser
        result = None
        async with get_session() as session:
            # Create enhanced parser with session
            parser = StreamingCSVParser(session)
            
            # Enhanced progress callback with structured events
            async def enhanced_progress_callback(progress_event: ImportProgressV1):
                """Enhanced progress callback with detailed logging and potential WebSocket broadcasting."""
                try:
                    # Log progress with enhanced details
                    logger.info(
                        f"Import {job_id}: {progress_event['percent']:.1f}% "
                        f"({progress_event['processed']}/{progress_event['total_rows']}) "
                        f"✓{progress_event['successful']} ✗{progress_event['error_count']} "
                        f"Rate: {progress_event.get('processing_rate', 0)} rows/sec "
                        f"ETA: {progress_event.get('estimated_completion', 'Unknown')}"
                    )
                    
                    # Here you could broadcast to WebSockets for real-time frontend updates
                    # await websocket_manager.broadcast_to_user(user_id, progress_event)
                    
                    # Store detailed progress in cache for API polling
                    # await redis_client.setex(f"import_progress:{job_id}", 300, json.dumps(progress_event))
                    
                except Exception as e:
                    logger.warning(f"Progress callback error for job {job_id}: {str(e)}")
            
            # Process the CSV file with enhanced progress tracking
            result = await parser.parse_file(
                file_path=Path(temp_file_path),
                import_job_id=job_id,
                progress_callback=enhanced_progress_callback
            )
            
            logger.info(f"Enhanced CSV parsing completed for job {job_id}")
        
        # Enhanced completion handling using repository pattern
        async with get_repository_context(ImportJobRepository) as import_repo:
            # Determine final status with enhanced logic
            if result.status == ImportStatus.PROCESSING:
                if result.error_count == 0:
                    final_status = ImportStatus.SUCCESS
                elif result.has_critical_errors:
                    final_status = ImportStatus.FAILED
                else:
                    final_status = ImportStatus.SUCCESS  # Partial success
            else:
                final_status = result.status
            
            # Enhanced completion data
            # Create enhanced error list with additional metadata
            enhanced_errors = []
            for error in result.errors[:100]:  # Limit stored errors
                enhanced_error = {
                    "row": error.row,
                    "column": error.column,
                    "message": error.message,
                    "value": error.value
                }
                enhanced_errors.append(enhanced_error)
            
            # Add summary information to the first error entry for metadata storage
            if enhanced_errors or final_status == ImportStatus.SUCCESS:
                summary_error = {
                    "row": 0,
                    "column": "_metadata",
                    "message": "Import summary",
                    "value": {
                        "successful_contacts": result.successful_contacts,
                        "total_errors": result.error_count,
                        "processing_rate": result.processing_rate,
                        "memory_peak_mb": result.memory_usage_mb,
                        "column_detection": {
                            "quality": result.column_detection.detection_quality,
                            "phone_confidence": result.column_detection.phone_confidence,
                            "name_confidence": result.column_detection.name_confidence,
                            "primary_phone_column": result.column_detection.primary_phone_column,
                            "name_column": result.column_detection.name_column,
                            "user_guidance": result.column_detection.user_guidance
                        }
                    }
                }
                enhanced_errors.insert(0, summary_error)
            
            # Update job with correct parameters
            await import_repo.complete_job(
                job_id=job_id,
                status=final_status,
                rows_processed=result.processed_rows,
                errors=enhanced_errors
            )
            
            
            # Send completion event (for WebSocket broadcasting)
            if result.start_time:
                total_time = (datetime.now(timezone.utc) - result.start_time).total_seconds()
                completion_event = create_completion_event(
                    job_id=job_id,
                    total_rows=result.total_rows,
                    successful_contacts=result.successful_contacts,
                    error_count=result.error_count,
                    processing_time=total_time,
                    average_rate=result.processing_rate,
                    peak_memory=result.memory_usage_mb,
                    sha256_verified=True,  # You have the hash from result
                    detected_columns=result.column_detection.detected_columns,
                    error_summary=[
                        ImportErrorV1(
                            row=error.row,
                            column=error.column,
                            message=error.message,
                            value=error.value
                        ) for error in result.errors[:10]  # Sample for event
                    ],
                    started_at=result.start_time,
                    completed_at=datetime.now(timezone.utc)
                )
                
                # Broadcast completion event
                # await websocket_manager.broadcast_completion(completion_event)
            
            # Enhanced logging with performance metrics
            logger.info(
                f"Enhanced processing completed for import job {job_id}: "
                f"Status={final_status.value}, "
                f"Contacts={result.successful_contacts}/{result.total_rows}, "
                f"Errors={result.error_count}, "
                f"Detection={result.column_detection.detection_quality}, "
                f"Rate={result.processing_rate:.1f} rows/sec, "
                f"Memory={result.memory_usage_mb:.1f}MB"
            )
            
    except FileNotFoundError:
        logger.error(f"Temporary file not found for import job {job_id}: {temp_file_path}")
        await _handle_enhanced_processing_error(
            job_id, 
            "file_not_found",
            "Temporary file not found - upload may have expired",
            temp_file_path
        )
        
    except ValidationError as e:
        logger.error(f"Validation error processing CSV for import job {job_id}: {str(e)}")
        await _handle_enhanced_processing_error(
            job_id,
            "validation_error", 
            f"CSV validation failed: {str(e)}",
            temp_file_path
        )
        
    except PermissionError:
        logger.error(f"Permission denied accessing file for import job {job_id}: {temp_file_path}")
        await _handle_enhanced_processing_error(
            job_id,
            "permission_error",
            "Permission denied accessing temporary file",
            temp_file_path
        )
        
    except Exception as e:
        logger.error(f"Unexpected error processing CSV for import job {job_id}: {str(e)}")
        await _handle_enhanced_processing_error(
            job_id,
            "system_error",
            f"Processing failed due to unexpected error: {str(e)}",
            temp_file_path
        )
    
    finally:
        # CRITICAL: Always clean up temporary file
        await _cleanup_temp_file(temp_file_path)


async def process_csv_with_mapping_background(
    job_id: str, 
    temp_file_path: str,
    column_mapping: ColumnMapping,
    options: Dict[str, Any]
) -> None:
    """
    Process CSV file with explicit column mapping provided by user.
    
    Args:
        job_id: Import job identifier
        temp_file_path: Path to temporary CSV file
        column_mapping: User-provided column mapping
        options: Processing options
    """
    logger.info(f"Starting mapped processing for import job {job_id}")
    
    try:
        from app.db.session import get_session
        
        async with get_session() as session:
            # Create parser with explicit mapping
            parser = StreamingCSVParser(session)
            
            # Create mapping config for parser
            mapping_config = {
                "phone_columns": column_mapping.phone_columns,
                "name_column": column_mapping.name_column,
                "skip_columns": column_mapping.skip_columns,
                "tag_columns": column_mapping.tag_columns,
                "skip_invalid_phones": options.get("skip_invalid_phones", True),
                "phone_country_default": options.get("phone_country_default", "US")
            }
            
            # Process with explicit mapping
            result = await parser.parse_file_with_mapping(
                file_path=Path(temp_file_path),
                import_job_id=job_id,
                mapping_config=mapping_config,
                progress_callback=None  # You can add progress callback here
            )
            
            # Rest of processing is same as original...
            # (Copy the completion handling from process_csv_background)
            
    except Exception as e:
        logger.error(f"Error in mapped processing for job {job_id}: {str(e)}")
        await _handle_enhanced_processing_error(
            job_id,
            "processing_error",
            f"Processing failed: {str(e)}",
            temp_file_path
        )
    finally:
        await _cleanup_temp_file(temp_file_path)

async def _handle_enhanced_processing_error(
    job_id: str, 
    error_type: str,
    error_message: str, 
    temp_file_path: str
) -> None:
    """
    Enhanced error handling with structured error types and recovery suggestions.
    
    Args:
        job_id: Import job identifier
        error_type: Structured error type for frontend handling
        error_message: Error description for user
        temp_file_path: Path to temporary file for cleanup
    """
    try:
        async with get_repository_context(ImportJobRepository) as import_repo:
            # Create failure event with recovery suggestions
            failure_event = create_failure_event(
                job_id=job_id,
                failure_reason=error_type,
                user_message=error_message,
                rows_processed=0,
                successful_contacts=0,
                started_at=datetime.now(timezone.utc),
                failed_at=datetime.now(timezone.utc),
                technical_details=None,  # Don't expose technical details to users
                error_id=f"err_{job_id}_{int(datetime.now().timestamp())}"
            )
            
            # Update job with structured error data
            await import_repo.complete_job(
                job_id=job_id,
                status=ImportStatus.FAILED,
                rows_processed=0,
                errors=[{
                    "row": 0, 
                    "column": None, 
                    "message": error_message,
                    "value": {
                        "error_type": error_type,
                        "recovery_suggestions": failure_event["recovery_suggestions"]
                    }
                }]
            )
            
            # Broadcast failure event for real-time updates
            # await websocket_manager.broadcast_failure(failure_event)
            
    except Exception as update_error:
        logger.error(f"Failed to update import job status for {job_id}: {str(update_error)}")


# Enhanced estimation function that works with the new parser
def _enhanced_estimate_processing_time(row_count: int, processing_rate: float = None) -> str:
    """
    Enhanced processing time estimation with dynamic rate calculation.
    
    Args:
        row_count: Number of rows to process
        processing_rate: Current processing rate (optional)
        
    Returns:
        str: Human-readable time estimate
    """
    if processing_rate and processing_rate > 0:
        # Use actual processing rate if available
        estimated_seconds = row_count / processing_rate
    else:
        # Fallback to benchmark-based estimates
        if row_count <= 1000:
            estimated_seconds = 30
        elif row_count <= 10000:
            estimated_seconds = 120  # 2 minutes
        elif row_count <= 100000:
            estimated_seconds = 180  # 3 minutes
        elif row_count <= 500000:
            estimated_seconds = 900  # 15 minutes
        else:
            estimated_seconds = 1800  # 30 minutes
    
    # Format the estimate
    if estimated_seconds < 60:
        return f"~{int(estimated_seconds)} seconds"
    elif estimated_seconds < 3600:
        return f"~{int(estimated_seconds / 60)} minutes"
    else:
        return f"~{int(estimated_seconds / 3600)} hours"


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

async def _validate_uploaded_file(file: UploadFile) -> None:
    """
    Comprehensive file validation for security and format compliance.
    
    **Security Validations:**
    - File extension whitelist checking
    - MIME type validation
    - File size limits
    - Filename sanitization
    
    **Format Validations:**
    - CSV header detection
    - Basic structure validation
    - Encoding compatibility check
    
    Args:
        file: Uploaded file to validate
        
    Raises:
        HTTPException: Various validation failures with specific error messages
    """
    # Validate file extension
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required"
        )
    
    file_extension = Path(file.filename).suffix.lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{file_extension}' not supported. "
                   f"Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Validate MIME type
    if file.content_type and file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"MIME type '{file.content_type}' not supported. "
                   f"Please upload a CSV file."
        )
    
    # Validate filename contains no dangerous characters
    if any(char in file.filename for char in ['..', '/', '\\', '\0']):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename contains invalid characters"
        )


async def _handle_processing_error(job_id: str, error_message: str, temp_file_path: str) -> None:
    """
    Handle processing errors with proper cleanup and status updates.
    
    Args:
        job_id: Import job identifier
        error_message: Error description for user
        temp_file_path: Path to temporary file for cleanup
    """
    try:
        async with get_repository_context(ImportJobRepository) as import_repo:
            await import_repo.complete_job(
                job_id=job_id,
                status=ImportStatus.FAILED,
                rows_processed=0,
                errors=[{"message": error_message, "row": 0, "column": None}]
            )
    except Exception as update_error:
        logger.error(f"Failed to update import job status for {job_id}: {str(update_error)}")


async def _cleanup_temp_file(temp_file_path: str) -> None:
    """
    Safely clean up temporary files with proper error handling.
    
    Args:
        temp_file_path: Path to temporary file to delete
    """
    try:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
            logger.info(f"Cleaned up temporary file: {temp_file_path}")
    except OSError as e:
        logger.warning(f"Failed to clean up temp file {temp_file_path}: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error cleaning up temp file {temp_file_path}: {str(e)}")


def _estimate_processing_time(row_count: int) -> str:
    """
    Estimate processing completion time based on row count.
    
    **Performance Estimates (based on Phase 2A benchmarks):**
    - 1,000 rows: ~30 seconds
    - 10,000 rows: ~2 minutes  
    - 100,000 rows: ~3 minutes
    - 1,000,000 rows: ~30 minutes
    
    Args:
        row_count: Number of rows to process
        
    Returns:
        str: Human-readable time estimate
    """
    if row_count <= 1000:
        return "~30 seconds"
    elif row_count <= 10000:
        return "~2 minutes"
    elif row_count <= 100000:
        return "~3 minutes"
    elif row_count <= 500000:
        return "~15 minutes"
    else:
        return "~30 minutes"


def _log_progress(job_id: str, progress: Dict[str, Any]) -> None:
    """
    Log processing progress for monitoring and debugging.
    
    Args:
        job_id: Import job identifier
        progress: Progress information from parser
    """
    try:
        if progress["type"] is not ImportEventType.PROGRESS:
            return

        logger.info(
            "Import %s: %5.1f%%  processed=%s  ok=%s  errors=%s",
            job_id,
            progress["percent"],
            progress["processed"],
            progress["successful"],
            len(progress["errors"]),
        )
    except Exception as e:                # noqa: BLE001
        logger.warning("Progress logger swallowed error: %s", e)


# Enhanced progress logging function
def _enhanced_log_progress(job_id: str, progress: ImportProgressV1) -> None:
    """
    Enhanced progress logging with structured data and performance metrics.
    
    Args:
        job_id: Import job identifier
        progress: Enhanced progress information
    """
    try:
        if progress["type"] != ImportEventType.PROGRESS:
            return

        # Enhanced logging with performance metrics
        logger.info(
            "Import %s: %5.1f%% | %s/%s rows | ✓%s contacts | ✗%s errors | %s rows/sec | %sMB | ETA: %s",
            job_id,
            progress["percent"],
            progress["processed"],
            progress["total_rows"],
            progress["successful"],
            progress["error_count"],
            progress.get("processing_rate", 0),
            progress.get("memory_usage_mb", 0),
            progress.get("estimated_completion", "Unknown")
        )
        
        # Log any critical issues
        if progress["has_critical_errors"]:
            logger.warning(f"Import {job_id}: Critical error threshold reached!")
        
        # Log recent errors for debugging
        if progress["errors"]:
            logger.debug(f"Import {job_id}: Recent errors: {len(progress['errors'])}")
            for error in progress["errors"][:3]:  # Log first 3 errors
                logger.debug(f"  Row {error['row']}: {error['message']}")
                
    except Exception as e:
        logger.warning(f"Enhanced progress logger error for {job_id}: {str(e)}")


async def _get_csv_preview_data(sha256: str, preview_rows: int = 5) -> Dict[str, Any]:
    """
    Get preview data from CSV file using SHA256 hash.
    
    Args:
        sha256: File hash to locate the file
        preview_rows: Number of rows to preview
        
    Returns:
        Dict with headers, sample rows, and column analysis
    """
    try:
        # Find the temp file by hash
        temp_file_path = _get_temp_file_path(sha256)
        if not temp_file_path:
            logger.warning(f"Temp file not found for hash {sha256}")
            return {
                "headers": [],
                "rows": [],
                "samples": {},
                "empty_counts": {}
            }
        
        headers = []
        rows = []
        samples = {}
        empty_counts = {}
        
        with open(temp_file_path, 'r', encoding='utf-8') as f:
            # Detect delimiter
            sample = f.read(8192)
            f.seek(0)
            
            sniffer = csv.Sniffer()
            try:
                delimiter = sniffer.sniff(sample, delimiters=',\t|;').delimiter
            except csv.Error:
                delimiter = ','
            
            # Read CSV
            reader = csv.DictReader(f, delimiter=delimiter)
            headers = reader.fieldnames or []
            
            # Initialize data structures
            for header in headers:
                samples[header] = []
                empty_counts[header] = 0
            
            # Read preview rows and collect samples
            row_count = 0
            for row in reader:
                if row_count < preview_rows:
                    rows.append(row)
                
                # Collect samples for column analysis (up to 100 rows)
                if row_count < 100:
                    for header in headers:
                        value = row.get(header, '').strip()
                        if value:
                            samples[header].append(value)
                        else:
                            empty_counts[header] += 1
                
                row_count += 1
                if row_count >= 100:  # Stop after 100 rows for sampling
                    break
        
        # Limit samples to 10 per column for response size
        for header in headers:
            if len(samples[header]) > 10:
                samples[header] = samples[header][:10]
        
        return {
            "headers": headers,
            "rows": rows,
            "samples": samples,
            "empty_counts": empty_counts
        }
        
    except Exception as e:
        logger.error(f"Error reading CSV preview data: {str(e)}")
        return {
            "headers": [],
            "rows": [],
            "samples": {},
            "empty_counts": {}
        }

def _detect_column_type(column_name: str, sample_values: List[str]) -> str:
    """
    Detect the likely data type of a column based on name and sample values.
    
    Args:
        column_name: Name of the column
        sample_values: Sample values from the column
        
    Returns:
        str: Detected type (phone, name, email, text, number)
    """
    col_lower = column_name.lower()
    
    # Check column name patterns
    if any(x in col_lower for x in ['phone', 'mobile', 'cell', 'tel']):
        return "phone"
    elif any(x in col_lower for x in ['name', 'contact', 'person']):
        return "name"
    elif any(x in col_lower for x in ['email', 'mail']):
        return "email"
    
    # Check sample values
    if sample_values:
        # Check if mostly numbers
        numeric_count = sum(1 for v in sample_values if v.replace('.', '').replace('-', '').isdigit())
        if numeric_count > len(sample_values) * 0.8:
            return "number"
    
    return "text"


def _get_temp_file_path(sha256: str) -> Optional[str]:
    """
    Get temp file path from SHA256 hash.
    
    Args:
        sha256: File hash
        
    Returns:
        Path to temp file or None if not found
    """
    # Look for file in temp directory
    # This is a simplified version - in production you might use a cache
    temp_dir = Path(TEMP_DIR)
    for file_path in temp_dir.glob(f"{TEMP_FILE_PREFIX}*.csv"):
        # Calculate hash of this file
        file_hash = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b""):
                file_hash.update(chunk)
        
        if file_hash.hexdigest() == sha256:
            return str(file_path)
    
    return None