"""
ImportJob repository for database operations related to CSV import jobs.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple
from uuid import uuid4

from sqlalchemy import select, update, delete, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

import logging
from app.utils.ids import generate_prefixed_id, IDPrefix
from app.db.repositories.base import BaseRepository
from app.models.import_job import ImportJob, ImportStatus
from app.schemas.import_job import ImportJobCreate, ImportJobUpdate

logger = logging.getLogger("inboxerr.db")


class ImportJobRepository(BaseRepository[ImportJob, ImportJobCreate, ImportJobUpdate]):
    """ImportJob repository for database operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize with session and ImportJob model."""
        super().__init__(session=session, model=ImportJob)
    
    async def get_by_owner(
        self, 
        owner_id: str, 
        status: Optional[ImportStatus] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[ImportJob], int]:
        """
        Get import jobs for a specific owner with optional filtering.
        
        Args:
            owner_id: Owner user ID
            status: Optional status filter
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[ImportJob], int]: (jobs, total_count)
        """
        # Build base query
        query = select(ImportJob).where(ImportJob.owner_id == owner_id)
        count_query = select(func.count(ImportJob.id)).where(ImportJob.owner_id == owner_id)
        
        # Apply status filter if provided
        if status:
            query = query.where(ImportJob.status == status)
            count_query = count_query.where(ImportJob.status == status)
        
        # Add ordering
        query = query.order_by(desc(ImportJob.created_at))
        
        # Get total count
        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        # Execute query
        result = await self.session.execute(query)
        jobs = result.scalars().all()
        
        return jobs, total
    
    async def get_active_jobs_count(self, owner_id: str) -> int:
        """
        Get count of active (processing) import jobs for a user.
        
        Args:
            owner_id: Owner user ID
            
        Returns:
            int: Number of active import jobs
        """
        result = await self.session.execute(
            select(func.count(ImportJob.id)).where(
                ImportJob.owner_id == owner_id,
                ImportJob.status == ImportStatus.PROCESSING
            )
        )
        return result.scalar() or 0
    
    async def update_progress(
        self,
        job_id: str,
        rows_processed: int,
        errors: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[ImportJob]:
        """
        Update import job progress.
        
        Args:
            job_id: Import job ID
            rows_processed: Number of rows processed
            errors: Optional list of error objects
            
        Returns:
            ImportJob: Updated import job or None
        """
        update_data = {
            "rows_processed": rows_processed,
            "updated_at": datetime.now(timezone.utc)
        }
        
        if errors is not None:
            update_data["errors"] = errors
        
        return await self.update(id=job_id, obj_in=update_data)
    
    async def complete_job(
        self,
        job_id: str,
        status: ImportStatus,
        rows_processed: int,
        errors: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[ImportJob]:
        """
        Mark an import job as completed with final status.
        
        Args:
            job_id: Import job ID
            status: Final status (SUCCESS, FAILED, CANCELLED)
            rows_processed: Final number of rows processed
            errors: Optional list of error objects
            
        Returns:
            ImportJob: Updated import job or None
        """
        update_data = {
            "status": status,
            "rows_processed": rows_processed,
            "completed_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        
        if errors is not None:
            update_data["errors"] = errors
        
        return await self.update(id=job_id, obj_in=update_data)
    
    async def get_by_hash(self, sha256: str, owner_id: str) -> Optional[ImportJob]:
        """
        Get import job by file hash and owner.
        
        Args:
            sha256: File SHA-256 hash
            owner_id: Owner user ID
            
        Returns:
            ImportJob: Found import job or None
        """
        result = await self.session.execute(
            select(ImportJob).where(
                ImportJob.sha256 == sha256,
                ImportJob.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()
    
    async def get_jobs_by_status(
        self,
        status: ImportStatus,
        limit: int = 100
    ) -> List[ImportJob]:
        """
        Get import jobs by status (useful for background processing).
        
        Args:
            status: Import status to filter by
            limit: Maximum number of jobs to return
            
        Returns:
            List[ImportJob]: List of import jobs
        """
        result = await self.session.execute(
            select(ImportJob)
            .where(ImportJob.status == status)
            .order_by(ImportJob.created_at)
            .limit(limit)
        )
        return result.scalars().all()
    
    async def cleanup_old_jobs(self, days_old: int = 30) -> int:
        """
        Clean up old completed import jobs.
        
        Args:
            days_old: Number of days old to consider for cleanup
            
        Returns:
            int: Number of jobs cleaned up
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
        
        # Delete old completed jobs
        result = await self.session.execute(
            delete(ImportJob).where(
                ImportJob.completed_at < cutoff_date,
                ImportJob.status.in_([ImportStatus.SUCCESS, ImportStatus.FAILED, ImportStatus.CANCELLED])
            )
        )
        
        deleted_count = result.rowcount
        
        logger.info(f"Cleaned up {deleted_count} old import jobs older than {days_old} days")
        return deleted_count
    
    async def get_user_statistics(self, owner_id: str) -> Dict[str, Any]:
        """
        Get import statistics for a user.
        
        Args:
            owner_id: Owner user ID
            
        Returns:
            Dict[str, Any]: Statistics dictionary
        """
        # Get counts by status
        status_counts = {}
        for status in ImportStatus:
            result = await self.session.execute(
                select(func.count(ImportJob.id)).where(
                    ImportJob.owner_id == owner_id,
                    ImportJob.status == status
                )
            )
            status_counts[status.value] = result.scalar() or 0
        
        # Get total contacts imported
        total_contacts_result = await self.session.execute(
            select(func.sum(ImportJob.rows_processed)).where(
                ImportJob.owner_id == owner_id,
                ImportJob.status == ImportStatus.SUCCESS
            )
        )
        total_contacts = total_contacts_result.scalar() or 0
        
        # Get recent activity (last 30 days)
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        recent_jobs_result = await self.session.execute(
            select(func.count(ImportJob.id)).where(
                ImportJob.owner_id == owner_id,
                ImportJob.created_at >= thirty_days_ago
            )
        )
        recent_jobs = recent_jobs_result.scalar() or 0
        
        return {
            "total_jobs": sum(status_counts.values()),
            "status_counts": status_counts,
            "total_contacts_imported": total_contacts,
            "recent_jobs_30_days": recent_jobs
        }
    
    async def create_import_job(
        self,
        *,
        id: Optional[str] = None, # Accept pre-generated ID
        filename: str,
        file_size: int,
        sha256: str,
        owner_id: str,
        rows_total: int = 0
    ) -> ImportJob:
        """
        Create a new import job with generated ID.
        
        Args:
            filename: Original filename
            file_size: File size in bytes
            sha256: File SHA-256 hash
            owner_id: Owner user ID
            rows_total: Total rows to process
            
        Returns:
            ImportJob: Created import job
        """
        job_id = id or generate_prefixed_id(IDPrefix.BATCH)  # Resuing ID from the top layer.
        
        db_obj = ImportJob(
            id=job_id,
            filename=filename,
            file_size=file_size,
            sha256=sha256,
            owner_id=owner_id,
            rows_total=rows_total,
            rows_processed=0,
            status=ImportStatus.PROCESSING,
            started_at=datetime.now(timezone.utc),
            errors=[]
        )
        
        self.session.add(db_obj)
        
        logger.info(f"Created import job {job_id} for user {owner_id}")
        return db_obj
    
    async def bulk_update_status(
        self,
        job_ids: List[str],
        status: ImportStatus,
        completed_at: Optional[datetime] = None
    ) -> int:
        """
        Bulk update status for multiple import jobs.
        
        Args:
            job_ids: List of import job IDs
            status: New status
            completed_at: Optional completion timestamp
            
        Returns:
            int: Number of jobs updated
        """
        if not job_ids:
            return 0
        
        update_data = {
            "status": status,
            "updated_at": datetime.now(timezone.utc)
        }
        
        if completed_at:
            update_data["completed_at"] = completed_at
        
        result = await self.session.execute(
            update(ImportJob)
            .where(ImportJob.id.in_(job_ids))
            .values(**update_data)
        )
        
        updated_count = result.rowcount
        
        logger.info(f"Bulk updated {updated_count} import jobs to status {status.value}")
        return updated_count


