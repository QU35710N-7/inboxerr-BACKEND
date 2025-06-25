# app/services/imports/service.py
"""
Import Service - Centralized transaction management for CSV imports.

This service owns the database session lifecycle and ensures all import operations
are properly transactional. One instance per import job, disposed after completion.
"""
import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import select, func

from app.db.session import get_session
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
    Centralized service for managing import operations with proper transaction boundaries.
    
    One instance per import job. Owns the database session for the entire import lifecycle.
    All database operations go through this service to ensure consistent transaction management.
    """
    
    def __init__(self, job_id: str):
        """
        Initialize import service for a specific job.
        
        Args:
            job_id: Import job identifier
        """
        self.job_id = job_id
        self.session: Optional[AsyncSession] = None
        self.import_repo: Optional[ImportJobRepository] = None
        self.contact_repo: Optional[ContactRepository] = None
        self._job: Optional[ImportJob] = None
        self._start_time = datetime.now(timezone.utc)
        self._last_milestone = 0
        
    async def __aenter__(self):
        """Context manager entry - creates session and repositories."""
        # Create session using the proper context manager
        self.session = await get_session().__aenter__()
        
        # Create repositories with the session
        self.import_repo = ImportJobRepository(self.session)
        self.contact_repo = ContactRepository(self.session)
        
        # Load the import job
        self._job = await self.import_repo.get_by_id(self.job_id)
        if not self._job:
            raise ValueError(f"Import job {self.job_id} not found")
            
        logger.info(f"ImportService initialized for job {self.job_id}")
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures session cleanup."""
        try:
            if exc_type is not None:
                # Exception occurred, rollback any pending transaction
                if self.session and self.session.in_transaction():
                    await self.session.rollback()
                    logger.warning(f"Rolled back transaction for job {self.job_id} due to exception")
            
            # Clean up session
            if self.session:
                await self.session.close()
                logger.debug(f"Closed session for import job {self.job_id}")
                
        except Exception as e:
            logger.error(f"Error cleaning up ImportService for job {self.job_id}: {str(e)}")
        finally:
            self.session = None
            self.import_repo = None
            self.contact_repo = None
            
    async def initialize_import(self, total_rows: int, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize import job with starting state.
        
        Args:
            total_rows: Total number of rows to process
            metadata: Optional metadata to store with job
        """
        try:
            async with self.session.begin():
                self._job.status = ImportStatus.PROCESSING
                self._job.rows_total = total_rows
                self._job.started_at = self._start_time
                
                # Store metadata in errors array (following existing pattern)
                if metadata:
                    self._job.errors = [{
                        "row": 0,
                        "column": "_metadata",
                        "message": "Import initialization",
                        "value": metadata
                    }]
                
                self.session.add(self._job)
                
            logger.info(f"Initialized import job {self.job_id} with {total_rows} total rows")
            
        except Exception as e:
            logger.error(f"Failed to initialize import job {self.job_id}: {str(e)}")
            raise
            
    async def process_contact_batch(
        self,
        contacts: List[Contact],
        batch_number: int,
        total_batches: int
    ) -> BatchResult:
        """
        Process a batch of contacts with atomic transaction handling.
        
        All operations in this method are atomic - either all succeed or all fail.
        
        Args:
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
            # Single transaction for the entire batch
            async with self.session.begin():
                # 1. Bulk insert contacts
                success_count = await self._bulk_insert_contacts(contacts)
                result.success_count = success_count
                result.error_count = len(contacts) - success_count
                
                # 2. Update import job progress
                self._job.rows_processed += len(contacts)
                
                # 3. Store any errors (if needed)
                if result.error_count > 0:
                    # For now, we'll track that some contacts failed
                    # In the future, we might want to identify specific failures
                    error = ImportError(
                        row=batch_number * IMPORT_BATCH_SIZE,
                        column="batch",
                        message=f"Batch {batch_number}: {result.error_count} contacts failed",
                        value=None
                    )
                    result.errors.append(error)
                
                # Add job to session for update
                self.session.add(self._job)
                
            # Calculate processing time
            end_time = datetime.now(timezone.utc)
            result.processing_time_ms = (end_time - start_time).total_seconds() * 1000
            
            # Log progress and check for milestones
            await self._log_progress(batch_number, total_batches)
            
            logger.debug(
                f"Batch {batch_number}/{total_batches} completed: "
                f"{result.success_count} successful, {result.error_count} errors, "
                f"{result.processing_time_ms:.1f}ms"
            )
            
        except IntegrityError as e:
            # Specific handling for duplicate contacts
            logger.warning(f"Batch {batch_number} had integrity errors (likely duplicates): {str(e)}")
            result.error_count = len(contacts)
            result.errors.append(ImportError(
                row=batch_number * IMPORT_BATCH_SIZE,
                column="batch",
                message="Batch failed due to duplicate contacts",
                value=str(e)
            ))
            
        except SQLAlchemyError as e:
            # Database-specific errors
            logger.error(f"Database error processing batch {batch_number}: {str(e)}")
            result.error_count = len(contacts)
            result.errors.append(ImportError(
                row=batch_number * IMPORT_BATCH_SIZE,
                column="batch",
                message=f"Database error: {str(e)}",
                value=None
            ))
            raise
            
        except Exception as e:
            # Unexpected errors
            logger.error(f"Unexpected error processing batch {batch_number}: {str(e)}")
            result.error_count = len(contacts)
            result.errors.append(ImportError(
                row=batch_number * IMPORT_BATCH_SIZE,
                column="batch",
                message=f"Unexpected error: {str(e)}",
                value=None
            ))
            raise
            
        return result
        
    async def complete_import(self, summary_stats: Dict[str, Any]) -> None:
        """
        Mark import as successfully completed with final statistics.
        
        Args:
            summary_stats: Final import statistics and metadata
        """
        try:
            async with self.session.begin():
                self._job.status = ImportStatus.SUCCESS
                self._job.completed_at = datetime.now(timezone.utc)
                
                # Add summary to errors array (following existing pattern)
                if self._job.errors is None:
                    self._job.errors = []
                    
                self._job.errors.append({
                    "row": 0,
                    "column": "_summary",
                    "message": "Import completed successfully",
                    "value": summary_stats
                })
                
                self.session.add(self._job)
                
            total_time = (datetime.now(timezone.utc) - self._start_time).total_seconds()
            logger.info(
                f"Import job {self.job_id} completed successfully: "
                f"{self._job.rows_processed} rows in {total_time:.1f}s"
            )
            
        except Exception as e:
            logger.error(f"Failed to complete import job {self.job_id}: {str(e)}")
            raise
            
    async def fail_import(self, error: Exception, error_context: Dict[str, Any]) -> None:
        """
        Mark import as failed with error context.
        
        Args:
            error: The exception that caused the failure
            error_context: Additional context about the failure
        """
        try:
            async with self.session.begin():
                self._job.status = ImportStatus.FAILED
                self._job.completed_at = datetime.now(timezone.utc)
                
                # Store failure information
                if self._job.errors is None:
                    self._job.errors = []
                    
                self._job.errors.append({
                    "row": 0,
                    "column": "_failure",
                    "message": str(error),
                    "value": error_context
                })
                
                self.session.add(self._job)
                
            logger.error(
                f"Import job {self.job_id} failed after processing "
                f"{self._job.rows_processed}/{self._job.rows_total} rows: {str(error)}"
            )
            
        except Exception as e:
            logger.error(f"Failed to mark import job {self.job_id} as failed: {str(e)}")
            # Don't raise here - we're already in error handling
            
    async def get_current_progress(self) -> Tuple[int, int]:
        """
        Get current progress (processed, total).
        
        Returns:
            Tuple of (rows_processed, rows_total)
        """
        return (self._job.rows_processed, self._job.rows_total)
        
    async def _bulk_insert_contacts(self, contacts: List[Contact]) -> int:
        """
        Bulk insert contacts using PostgreSQL's ON CONFLICT DO NOTHING.
        
        Args:
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
                
        # Prepare contact data for bulk insert
        contact_dicts = [
            {
                'id': contact.id,
                'import_id': contact.import_id,
                'phone': contact.phone,
                'name': contact.name,
                'tags': contact.tags,
                'csv_row_number': contact.csv_row_number,
                'raw_data': contact.raw_data,
            }
            for contact in contacts
        ]
        
        # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
        from sqlalchemy.dialects.postgresql import insert
        
        stmt = insert(Contact.__table__).values(contact_dicts)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=['import_id', 'phone']  # Unique constraint
        )
        
        # Execute the insert
        result = await self.session.execute(stmt)
        
        # Count how many were actually inserted
        # PostgreSQL's INSERT returns rowcount of inserted rows (excluding conflicts)
        return result.rowcount
        
    async def _log_progress(self, batch_number: int, total_batches: int) -> None:
        """Log progress and emit milestone events."""
        if self._job.rows_total == 0:
            return
            
        progress_percent = (self._job.rows_processed / self._job.rows_total) * 100
        
        # Check for 10% milestones
        milestone = int(progress_percent // 10) * 10
        if milestone > self._last_milestone and milestone > 0:
            self._last_milestone = milestone
            logger.info(
                f"Import {self.job_id} reached {milestone}% "
                f"({self._job.rows_processed:,}/{self._job.rows_total:,} rows)"
            )
            
    async def update_detection_metadata(self, detection_data: Dict[str, Any]) -> None:
        """
        Update import job with column detection metadata.
        
        Args:
            detection_data: Column detection results
        """
        try:
            async with self.session.begin():
                if self._job.errors is None:
                    self._job.errors = []
                    
                # Find and update or add detection metadata
                detection_updated = False
                for error in self._job.errors:
                    if error.get("column") == "_metadata":
                        error["value"].update({"column_detection": detection_data})
                        detection_updated = True
                        break
                        
                if not detection_updated:
                    self._job.errors.append({
                        "row": 0,
                        "column": "_metadata",
                        "message": "Column detection",
                        "value": {"column_detection": detection_data}
                    })
                    
                self.session.add(self._job)
                
        except Exception as e:
            logger.error(f"Failed to update detection metadata for job {self.job_id}: {str(e)}")
            raise