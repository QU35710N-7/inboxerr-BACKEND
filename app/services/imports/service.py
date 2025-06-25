# app/services/imports/service.py
"""
Import Service - Stateless coordinator for CSV imports.

This service coordinates import operations using the repository pattern with short-lived sessions.
Follows the existing codebase architecture for consistency and proper connection management.
"""
import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass

from app.db.session import get_repository_context
from app.db.repositories.import_jobs import ImportJobRepository
from app.db.repositories.contacts import ContactRepository
from app.models.import_job import ImportJob, ImportStatus
from app.models.contact import Contact
from app.schemas.import_job import ImportError
from app.utils.ids import generate_prefixed_id, IDPrefix

logger = logging.getLogger("inboxerr.imports.service")

# Configuration from environment
IMPORT_BATCH_SIZE = int(os.getenv('INBOXERR_IMPORT_BATCH_SIZE', '1000'))


@dataclass
class BatchResult:
    """Result of processing a single batch of contacts."""
    success_count: int
    error_count: int
    errors: List[ImportError]
    batch_number: int
    processing_time_ms: float


class ImportService:
    """
    Stateless coordinator for import operations following repository pattern.
    
    Uses short-lived sessions through get_repository_context() for each operation.
    No session lifecycle management - follows existing codebase architecture.
    """
    
    @staticmethod
    async def initialize_import(
        job_id: str, 
        total_rows: int, 
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Initialize import job with starting state.
        
        Args:
            job_id: Import job identifier
            total_rows: Total number of rows to process
            metadata: Optional metadata to store with job
        """
        try:
            async with get_repository_context(ImportJobRepository) as import_repo:
                # Get the job
                job = await import_repo.get_by_id(job_id)
                if not job:
                    raise ValueError(f"Import job {job_id} not found")
                
                # Update job with initialization data
                update_data = {
                    "status": ImportStatus.PROCESSING,
                    "rows_total": total_rows,
                    "started_at": datetime.now(timezone.utc)
                }
                
                # Store metadata in errors array (following existing pattern)
                if metadata:
                    update_data["errors"] = [{
                        "row": 0,
                        "column": "_metadata",
                        "message": "Import initialization",
                        "value": metadata
                    }]
                
                await import_repo.update(id=job_id, obj_in=update_data)
                
            logger.info(f"Initialized import job {job_id} with {total_rows} total rows")
            
        except Exception as e:
            logger.error(f"Failed to initialize import job {job_id}: {str(e)}")
            raise
            
    @staticmethod
    async def process_contact_batch(
        job_id: str,
        contacts: List[Contact],
        batch_number: int,
        total_batches: int
    ) -> BatchResult:
        """
        Process a batch of contacts with atomic transaction handling.
        
        Uses single repository context for atomic operation.
        
        Args:
            job_id: Import job identifier
            contacts: List of contacts to insert
            batch_number: Current batch number (1-based)
            total_batches: Total number of batches expected
            
        Returns:
            BatchResult: Result of batch processing
        """
        start_time = datetime.now(timezone.utc)
        result = BatchResult(
            success_count=0,
            error_count=0,
            errors=[],
            batch_number=batch_number,
            processing_time_ms=0
        )
        
        if not contacts:
            return result
            
        try:
            # Use dual repository context for atomic batch processing
            async with get_repository_context(ImportJobRepository) as import_repo, \
                       get_repository_context(ContactRepository) as contact_repo:
                
                # 1. Get current job state
                job = await import_repo.get_by_id(job_id)
                if not job:
                    raise ValueError(f"Import job {job_id} not found")
                
                # 2. Bulk insert contacts
                success_count = await ImportService._bulk_insert_contacts(
                    contact_repo, contacts
                )
                result.success_count = success_count
                result.error_count = len(contacts) - success_count
                
                # 3. Update import job progress
                new_rows_processed = job.rows_processed + len(contacts)
                update_data = {"rows_processed": new_rows_processed}
                
                # 4. Store batch errors if any
                if result.error_count > 0:
                    error = ImportError(
                        row=batch_number * IMPORT_BATCH_SIZE,
                        column="batch",
                        message=f"Batch {batch_number}: {result.error_count} contacts failed",
                        value=None
                    )
                    result.errors.append(error)
                    
                    # Add to job errors
                    current_errors = job.errors or []
                    current_errors.append({
                        "row": batch_number * IMPORT_BATCH_SIZE,
                        "column": "batch",
                        "message": f"Batch {batch_number}: {result.error_count} contacts failed",
                        "value": None
                    })
                    update_data["errors"] = current_errors
                
                # Update job
                await import_repo.update(id=job_id, obj_in=update_data)
                
            # Calculate processing time
            end_time = datetime.now(timezone.utc)
            result.processing_time_ms = (end_time - start_time).total_seconds() * 1000
            
            # Log progress
            await ImportService._log_progress(job_id, batch_number, total_batches)
            
            logger.debug(
                f"Batch {batch_number}/{total_batches} completed: "
                f"{result.success_count} successful, {result.error_count} errors, "
                f"{result.processing_time_ms:.1f}ms"
            )
            
        except Exception as e:
            # Handle various error types
            logger.error(f"Error processing batch {batch_number} for job {job_id}: {str(e)}")
            result.error_count = len(contacts)
            result.errors.append(ImportError(
                row=batch_number * IMPORT_BATCH_SIZE,
                column="batch",
                message=f"Batch processing failed: {str(e)}",
                value=None
            ))
            raise
            
        return result
        
    @staticmethod
    async def complete_import(job_id: str, summary_stats: Dict[str, Any]) -> None:
        """
        Mark import as successfully completed with final statistics.
        
        Args:
            job_id: Import job identifier
            summary_stats: Final import statistics and metadata
        """
        try:
            async with get_repository_context(ImportJobRepository) as import_repo:
                # Get current job
                job = await import_repo.get_by_id(job_id)
                if not job:
                    raise ValueError(f"Import job {job_id} not found")
                
                # Prepare completion data
                current_errors = job.errors or []
                current_errors.append({
                    "row": 0,
                    "column": "_summary",
                    "message": "Import completed successfully",
                    "value": summary_stats
                })
                
                update_data = {
                    "status": ImportStatus.SUCCESS,
                    "completed_at": datetime.now(timezone.utc),
                    "errors": current_errors
                }
                
                await import_repo.update(id=job_id, obj_in=update_data)
                
            # Calculate total time from job data
            async with get_repository_context(ImportJobRepository) as import_repo:
                final_job = await import_repo.get_by_id(job_id)
                if final_job and final_job.started_at:
                    total_time = (datetime.now(timezone.utc) - final_job.started_at).total_seconds()
                    logger.info(
                        f"Import job {job_id} completed successfully: "
                        f"{final_job.rows_processed} rows in {total_time:.1f}s"
                    )
                
        except Exception as e:
            logger.error(f"Failed to complete import job {job_id}: {str(e)}")
            raise
            
    @staticmethod
    async def fail_import(job_id: str, error: Exception, error_context: Dict[str, Any]) -> None:
        """
        Mark import as failed with error context.
        
        Args:
            job_id: Import job identifier
            error: The exception that caused the failure
            error_context: Additional context about the failure
        """
        try:
            async with get_repository_context(ImportJobRepository) as import_repo:
                # Get current job
                job = await import_repo.get_by_id(job_id)
                if not job:
                    logger.warning(f"Import job {job_id} not found during failure handling")
                    return
                
                # Prepare failure data
                current_errors = job.errors or []
                current_errors.append({
                    "row": 0,
                    "column": "_failure",
                    "message": str(error),
                    "value": error_context
                })
                
                update_data = {
                    "status": ImportStatus.FAILED,
                    "completed_at": datetime.now(timezone.utc),
                    "errors": current_errors
                }
                
                await import_repo.update(id=job_id, obj_in=update_data)
                
            logger.error(f"Import job {job_id} marked as failed: {str(error)}")
            
        except Exception as e:
            logger.error(f"Failed to mark import job {job_id} as failed: {str(e)}")
            # Don't raise here - we're already in error handling
            
    @staticmethod
    async def get_current_progress(job_id: str) -> Tuple[int, int]:
        """
        Get current progress (processed, total).
        
        Args:
            job_id: Import job identifier
            
        Returns:
            Tuple of (rows_processed, rows_total)
        """
        try:
            async with get_repository_context(ImportJobRepository) as import_repo:
                job = await import_repo.get_by_id(job_id)
                if not job:
                    return (0, 0)
                return (job.rows_processed, job.rows_total)
        except Exception as e:
            logger.error(f"Failed to get progress for job {job_id}: {str(e)}")
            return (0, 0)
        
    @staticmethod
    async def update_detection_metadata(job_id: str, detection_data: Dict[str, Any]) -> None:
        """
        Update import job with column detection metadata.
        
        Args:
            job_id: Import job identifier
            detection_data: Column detection results
        """
        try:
            async with get_repository_context(ImportJobRepository) as import_repo:
                # Get current job
                job = await import_repo.get_by_id(job_id)
                if not job:
                    raise ValueError(f"Import job {job_id} not found")
                
                current_errors = job.errors or []
                
                # Find and update or add detection metadata
                detection_updated = False
                for error in current_errors:
                    if error.get("column") == "_metadata":
                        if "value" not in error:
                            error["value"] = {}
                        error["value"]["column_detection"] = detection_data
                        detection_updated = True
                        break
                        
                if not detection_updated:
                    current_errors.append({
                        "row": 0,
                        "column": "_metadata",
                        "message": "Column detection",
                        "value": {"column_detection": detection_data}
                    })
                    
                await import_repo.update(id=job_id, obj_in={"errors": current_errors})
                
        except Exception as e:
            logger.error(f"Failed to update detection metadata for job {job_id}: {str(e)}")
            raise
            
    @staticmethod
    async def _bulk_insert_contacts(contact_repo: ContactRepository, contacts: List[Contact]) -> int:
        """
        Bulk insert contacts using repository method.
        
        Args:
            contact_repo: Contact repository instance
            contacts: List of contacts to insert
            
        Returns:
            Number of successfully inserted contacts
        """
        if not contacts:
            return 0
            
        # Generate IDs if not set
        for contact in contacts:
            if not contact.id:
                contact.id = generate_prefixed_id(IDPrefix.CONTACT)
        
        # Use repository's bulk insert method
        try:
            created_count, skipped_count, error_phones = await contact_repo.bulk_create_contacts(
                contacts, ignore_duplicates=True
            )
            return created_count
        except Exception as e:
            logger.error(f"Bulk insert failed: {str(e)}")
            # Fallback: try individual inserts
            success_count = 0
            for contact in contacts:
                try:
                    await contact_repo.create(contact)
                    success_count += 1
                except Exception as individual_error:
                    logger.debug(f"Individual contact insert failed: {str(individual_error)}")
                    continue
            return success_count
        
    @staticmethod
    async def _log_progress(job_id: str, batch_number: int, total_batches: int) -> None:
        """Log progress and emit milestone events."""
        try:
            async with get_repository_context(ImportJobRepository) as import_repo:
                job = await import_repo.get_by_id(job_id)
                if not job or job.rows_total == 0:
                    return
                    
                progress_percent = (job.rows_processed / job.rows_total) * 100
                
                # Log milestone progress
                milestone = int(progress_percent // 10) * 10
                if milestone > 0 and milestone % 10 == 0:
                    logger.info(
                        f"Import {job_id} reached {milestone}% "
                        f"({job.rows_processed:,}/{job.rows_total:,} rows)"
                    )
        except Exception as e:
            logger.debug(f"Progress logging failed for job {job_id}: {str(e)}")
            # Don't raise - progress logging is not critical