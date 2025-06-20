# app/api/v1/endpoints/imports.py
"""
CSV Import streaming endpoints.
Phase 1A implementation - streaming file uploads without memory exhaustion.
"""
import os
import csv
import hashlib
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import get_current_user, get_rate_limiter
from app.core.exceptions import ValidationError
from app.schemas.user import User
from app.utils.ids import generate_prefixed_id, IDPrefix
from app.utils.datetime import utc_now

router = APIRouter()

# Constants for file processing
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
MAX_ROW_COUNT = 1_000_000  # 1M rows
ALLOWED_EXTENSIONS = {".csv"}
ALLOWED_MIME_TYPES = {"text/csv", "text/plain", "application/csv"}
CHUNK_SIZE = 8192  # 8KB chunks for streaming

# TODO: In Phase 2, we'll need a proper import_jobs table
# For now, we'll use in-memory tracking (not production-ready)
_active_import_jobs: Dict[str, Dict[str, Any]] = {}


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_csv_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    rate_limiter = Depends(get_rate_limiter),
) -> JSONResponse:
    """
    Upload a CSV file for streaming processing.
    
    Phase 1A: Stream file to temporary storage, validate, compute hash.
    Returns 202 immediately with job tracking info.
    
    - **file**: CSV file to upload (max 100MB)
    
    Returns:
        - **job_id**: Unique job identifier for tracking
        - **file_hash**: SHA-256 hash of uploaded file
        - **row_count**: Number of data rows detected
        - **headers**: CSV column headers
        - **status**: Job status (UPLOADED)
    """
    # Check rate limits
    await rate_limiter.check_rate_limit(current_user.id, "csv_upload")
    
    # Validate file extension
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filename is required"
        )
    
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid file extension. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Validate MIME type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid content type. Allowed: {', '.join(ALLOWED_MIME_TYPES)}"
        )
    
    # Check if user has too many active jobs (rate limiting)
    user_active_jobs = sum(
        1 for job in _active_import_jobs.values() 
        if job.get("user_id") == current_user.id and job.get("status") == "PROCESSING"
    )
    
    if user_active_jobs >= 5:  # Max 5 concurrent uploads per user
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many active import jobs. Please wait for existing jobs to complete."
        )
    
    # Generate job ID and create temporary file
    job_id = generate_prefixed_id(IDPrefix.BATCH)  # Reusing BATCH prefix for import jobs
    
    try:
        # Create temporary file
        temp_fd, temp_path = tempfile.mkstemp(suffix=".csv", prefix=f"import_{job_id}_")
        
        # Stream file to temporary storage with size checking
        file_hash = hashlib.sha256()
        total_size = 0
        
        with os.fdopen(temp_fd, 'wb') as temp_file:
            while chunk := await file.read(CHUNK_SIZE):
                total_size += len(chunk)
                
                # Check file size limit
                if total_size > MAX_FILE_SIZE:
                    os.unlink(temp_path)  # Clean up temp file
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB"
                    )
                
                file_hash.update(chunk)
                temp_file.write(chunk)
        
        # Reset file position for CSV analysis
        await file.seek(0)
        
        # Analyze CSV structure (peek at headers and count rows)
        headers = []
        row_count = 0
        
        try:
            # Read and analyze CSV without loading full file into memory
            content = await file.read(CHUNK_SIZE)  # Read first chunk for headers
            await file.seek(0)
            
            # Detect delimiter
            sample = content.decode('utf-8', errors='ignore')
            delimiter = ','
            if '\t' in sample and sample.count('\t') > sample.count(','):
                delimiter = '\t'
            elif ';' in sample and sample.count(';') > sample.count(','):
                delimiter = ';'
            
            # Parse headers from first line
            first_line = sample.split('\n')[0].strip()
            if first_line:
                headers = [col.strip() for col in first_line.split(delimiter)]
            
            # Count rows using the temporary file
            with open(temp_path, 'r', encoding='utf-8') as f:
                csv_reader = csv.reader(f, delimiter=delimiter)
                
                # Skip header if it exists
                if headers:
                    next(csv_reader, None)
                
                # Count remaining rows
                for row in csv_reader:
                    if any(cell.strip() for cell in row):  # Skip empty rows
                        row_count += 1
                        
                        # Check row count limit
                        if row_count > MAX_ROW_COUNT:
                            os.unlink(temp_path)  # Clean up
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Too many rows. Maximum: {MAX_ROW_COUNT:,}"
                            )
        
        except UnicodeDecodeError:
            os.unlink(temp_path)  # Clean up
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="File encoding not supported. Please use UTF-8 encoding."
            )
        except Exception as e:
            os.unlink(temp_path)  # Clean up
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Error analyzing CSV file: {str(e)}"
            )
        
        # Store job information (in Phase 2, this will go to database)
        job_info = {
            "job_id": job_id,
            "user_id": current_user.id,
            "filename": file.filename,
            "temp_path": temp_path,
            "file_hash": file_hash.hexdigest(),
            "file_size": total_size,
            "row_count": row_count,
            "headers": headers,
            "status": "UPLOADED",
            "created_at": utc_now().isoformat(),
            "errors": []
        }
        
        _active_import_jobs[job_id] = job_info
        
        # Return 202 with job tracking info
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job_id,
                "file_hash": file_hash.hexdigest(),
                "file_size": total_size,
                "row_count": row_count,
                "headers": headers,
                "status": "UPLOADED",
                "message": "File uploaded successfully. Use job_id to track processing status."
            }
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Clean up temp file on any other error
        if 'temp_path' in locals():
            try:
                os.unlink(temp_path)
            except:
                pass
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing upload: {str(e)}"
        )


@router.get("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def get_import_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Get the status of an import job.
    
    - **job_id**: Import job identifier
    
    Returns job status and progress information.
    """
    # Get job info
    job_info = _active_import_jobs.get(job_id)
    if not job_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found"
        )
    
    # Check ownership
    if job_info["user_id"] != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this import job"
        )
    
    # Return job status (without sensitive temp_path)
    return {
        "job_id": job_info["job_id"],
        "filename": job_info["filename"],
        "file_hash": job_info["file_hash"],
        "file_size": job_info["file_size"],
        "row_count": job_info["row_count"],
        "headers": job_info["headers"],
        "status": job_info["status"],
        "created_at": job_info["created_at"],
        "errors": job_info.get("errors", [])
    }


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_import_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Cancel an import job and clean up temporary files.
    
    - **job_id**: Import job identifier
    """
    # Get job info
    job_info = _active_import_jobs.get(job_id)
    if not job_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found"
        )
    
    # Check ownership
    if job_info["user_id"] != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel this import job"
        )
    
    # Clean up temporary file
    temp_path = job_info.get("temp_path")
    if temp_path and os.path.exists(temp_path):
        try:
            os.unlink(temp_path)
        except Exception:
            pass  # File might already be deleted
    
    # Update job status
    job_info["status"] = "CANCELLED"
    
    # Remove from active jobs (cleanup)
    del _active_import_jobs[job_id]


@router.get("/jobs", status_code=status.HTTP_200_OK)
async def list_import_jobs(
    current_user: User = Depends(get_current_user),
    status_filter: Optional[str] = Query(None, description="Filter by job status"),
) -> Dict[str, Any]:
    """
    List import jobs for the current user.
    
    - **status_filter**: Optional status filter (UPLOADED, PROCESSING, SUCCESS, FAILED, CANCELLED)
    
    Returns list of import jobs.
    """
    # Filter jobs for current user
    user_jobs = [
        {
            "job_id": job["job_id"],
            "filename": job["filename"],
            "file_hash": job["file_hash"],
            "file_size": job["file_size"],
            "row_count": job["row_count"],
            "headers": job["headers"],
            "status": job["status"],
            "created_at": job["created_at"],
            "errors": job.get("errors", [])
        }
        for job in _active_import_jobs.values()
        if job["user_id"] == current_user.id
    ]
    
    # Apply status filter if provided
    if status_filter:
        user_jobs = [job for job in user_jobs if job["status"] == status_filter.upper()]
    
    # Sort by creation time (newest first)
    user_jobs.sort(key=lambda x: x["created_at"], reverse=True)
    
    return {
        "jobs": user_jobs,
        "total": len(user_jobs)
    }