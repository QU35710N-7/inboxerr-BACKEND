"""
Contact repository for database operations related to imported contacts.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple
from uuid import uuid4

from sqlalchemy import select, update, delete, and_, or_, desc, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import logging
from app.utils.ids import generate_prefixed_id, IDPrefix
from app.db.repositories.base import BaseRepository
from app.models.contact import Contact
from app.schemas.contact import ContactCreate, ContactUpdate

logger = logging.getLogger("inboxerr.db")


class ContactRepository(BaseRepository[Contact, ContactCreate, ContactUpdate]):
    """Contact repository for database operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize with session and Contact model."""
        super().__init__(session=session, model=Contact)
    
    async def get_by_import_id(
        self,
        import_id: str,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[Contact], int]:
        """
        Get contacts for a specific import job with pagination.
        
        Args:
            import_id: Import job ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Contact], int]: (contacts, total_count)
        """
        # Build queries
        query = select(Contact).where(Contact.import_id == import_id)
        count_query = select(func.count(Contact.id)).where(Contact.import_id == import_id)
        
        # Add ordering
        query = query.order_by(desc(Contact.created_at))
        
        # Get total count
        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        # Execute query
        result = await self.session.execute(query)
        contacts = result.scalars().all()
        
        return contacts, total
    
    async def get_by_phone(self, phone: str, import_id: Optional[str] = None) -> Optional[Contact]:
        """
        Get contact by phone number, optionally within a specific import.
        
        Args:
            phone: Phone number to search for
            import_id: Optional import ID to scope search
            
        Returns:
            Contact: Found contact or None
        """
        query = select(Contact).where(Contact.phone == phone)
        
        if import_id:
            query = query.where(Contact.import_id == import_id)
        
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def search_contacts(
        self,
        import_id: Optional[str] = None,
        phone_query: Optional[str] = None,
        name_query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[Contact], int]:
        """
        Search contacts with various filters.
        
        Args:
            import_id: Optional import ID filter
            phone_query: Optional phone number search (partial match)
            name_query: Optional name search (partial match)
            tags: Optional list of tags to filter by (OR operation)
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            Tuple[List[Contact], int]: (contacts, total_count)
        """
        # Build base query
        query = select(Contact)
        count_query = select(func.count(Contact.id))
        
        conditions = []
        
        # Apply filters
        if import_id:
            conditions.append(Contact.import_id == import_id)
        
        if phone_query:
            conditions.append(Contact.phone.ilike(f"%{phone_query}%"))
        
        if name_query:
            conditions.append(Contact.name.ilike(f"%{name_query}%"))
        
        if tags:
            # Search for any of the provided tags in the tags JSON array
            tag_conditions = []
            for tag in tags:
                tag_conditions.append(Contact.tags.op('?')(tag))
            conditions.append(or_(*tag_conditions))
        
        # Apply all conditions
        if conditions:
            query = query.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))
        
        # Add ordering
        query = query.order_by(desc(Contact.created_at))
        
        # Get total count
        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        # Execute query
        result = await self.session.execute(query)
        contacts = result.scalars().all()
        
        return contacts, total
    
    async def bulk_create_contacts(
        self,
        contacts: List[Contact],
        ignore_duplicates: bool = True
    ) -> Tuple[int, int, List[str]]:
        """
        Bulk create contacts with duplicate handling.
        
        Args:
            contacts: List of Contact objects to create
            ignore_duplicates: Whether to ignore duplicate phone numbers
            
        Returns:
            Tuple[int, int, List[str]]: (created_count, skipped_count, error_phones)
        """
        if not contacts:
            return 0, 0, []
        
        created_count = 0
        skipped_count = 0
        error_phones = []
        
        if ignore_duplicates:
            # Insert contacts one by one to handle duplicates gracefully
            for contact in contacts:
                try:
                    # Check if contact already exists
                    existing = await self.get_by_phone(contact.phone, contact.import_id)
                    if existing:
                        skipped_count += 1
                        logger.debug(f"Skipping duplicate contact: {contact.phone}")
                        continue
                    
                    self.session.add(contact)
                    created_count += 1
                    
                except IntegrityError:
                    await self.session.rollback()
                    skipped_count += 1
                    logger.debug(f"Constraint violation for contact: {contact.phone}")
                except SQLAlchemyError as e:
                    await self.session.rollback()
                    error_phones.append(contact.phone)
                    logger.error(f"Failed to create contact {contact.phone}: {str(e)}")
        else:
            # Try bulk insert first
            try:
                self.session.add_all(contacts)
                created_count = len(contacts)
            except IntegrityError:
                await self.session.rollback()
                # Fall back to individual inserts
                created_count, skipped_count, error_phones = await self.bulk_create_contacts(
                    contacts, ignore_duplicates=True
                )
        
        logger.info(f"Bulk contact creation: {created_count} created, {skipped_count} skipped, {len(error_phones)} errors")
        return created_count, skipped_count, error_phones
    
    async def get_contacts_count_by_import(self, import_id: str) -> int:
        """
        Get total count of contacts for an import job.
        
        Args:
            import_id: Import job ID
            
        Returns:
            int: Number of contacts
        """
        result = await self.session.execute(
            select(func.count(Contact.id)).where(Contact.import_id == import_id)
        )
        return result.scalar() or 0
    
    async def get_duplicate_phones(self, import_id: str) -> List[Dict[str, Any]]:
        """
        Find duplicate phone numbers within an import.
        
        Args:
            import_id: Import job ID
            
        Returns:
            List[Dict[str, Any]]: List of duplicate phone info
        """
        # Query to find phone numbers that appear more than once
        query = text("""
            SELECT phone, COUNT(*) as count, array_agg(id) as contact_ids
            FROM contact 
            WHERE import_id = :import_id 
            GROUP BY phone 
            HAVING COUNT(*) > 1
            ORDER BY count DESC
        """)
        
        result = await self.session.execute(query, {"import_id": import_id})
        duplicates = []
        
        for row in result:
            duplicates.append({
                "phone": row.phone,
                "count": row.count,
                "contact_ids": row.contact_ids
            })
        
        return duplicates
    
    async def delete_contacts_by_import(self, import_id: str) -> int:
        """
        Delete all contacts for a specific import job.
        
        Args:
            import_id: Import job ID
            
        Returns:
            int: Number of contacts deleted
        """
        result = await self.session.execute(
            delete(Contact).where(Contact.import_id == import_id)
        )
        
        deleted_count = result.rowcount
        
        logger.info(f"Deleted {deleted_count} contacts for import {import_id}")
        return deleted_count
    
    async def update_contact_tags(
        self,
        contact_id: str,
        tags: List[str]
    ) -> Optional[Contact]:
        """
        Update tags for a specific contact.
        
        Args:
            contact_id: Contact ID
            tags: New list of tags
            
        Returns:
            Contact: Updated contact or None
        """
        # Remove duplicates and empty tags
        clean_tags = list(set([tag.strip() for tag in tags if tag.strip()]))
        
        return await self.update(
            id=contact_id,
            obj_in={"tags": clean_tags}
        )
    
    async def add_tag_to_contacts(
        self,
        import_id: str,
        tag: str,
        phone_numbers: Optional[List[str]] = None
    ) -> int:
        """
        Add a tag to multiple contacts.
        
        Args:
            import_id: Import job ID
            tag: Tag to add
            phone_numbers: Optional list of specific phone numbers to tag
            
        Returns:
            int: Number of contacts updated
        """
        if not tag.strip():
            return 0
        
        # Build query conditions
        conditions = [Contact.import_id == import_id]
        if phone_numbers:
            conditions.append(Contact.phone.in_(phone_numbers))
        
        # Get contacts to update
        query = select(Contact).where(and_(*conditions))
        result = await self.session.execute(query)
        contacts = result.scalars().all()
        
        updated_count = 0
        for contact in contacts:
            current_tags = contact.tags or []
            if tag not in current_tags:
                current_tags.append(tag)
                contact.tags = current_tags
                updated_count += 1
        
        
        logger.info(f"Added tag '{tag}' to {updated_count} contacts in import {import_id}")
        return updated_count
    
    async def get_tags_summary(self, import_id: str) -> Dict[str, int]:
        """
        Get summary of all tags used in an import.
        
        Args:
            import_id: Import job ID
            
        Returns:
            Dict[str, int]: Tag name to count mapping
        """
        # This requires a more complex query to extract tags from JSON arrays
        query = text("""
            SELECT tag, COUNT(*) as count
            FROM contact, jsonb_array_elements_text(
                CASE 
                    WHEN tags IS NULL THEN '[]'::jsonb 
                    ELSE tags::jsonb 
                END
            ) AS tag
            WHERE import_id = :import_id
            GROUP BY tag
            ORDER BY count DESC
        """)
        
        result = await self.session.execute(query, {"import_id": import_id})
        tag_counts = {}
        
        for row in result:
            tag_counts[row.tag] = row.count
        
        return tag_counts
    
    async def export_contacts_to_dict(
        self,
        import_id: str,
        include_raw_data: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Export contacts to dictionary format for CSV/JSON export.
        
        Args:
            import_id: Import job ID
            include_raw_data: Whether to include original CSV data
            
        Returns:
            List[Dict[str, Any]]: List of contact dictionaries
        """
        contacts, _ = await self.get_by_import_id(import_id, limit=10000)  # Large limit for export
        
        export_data = []
        for contact in contacts:
            contact_dict = {
                "id": contact.id,
                "phone": contact.phone,
                "name": contact.name or "",
                "tags": ",".join(contact.tags or []),
                "csv_row_number": contact.csv_row_number,
                "created_at": contact.created_at.isoformat()
            }
            
            if include_raw_data and contact.raw_data:
                contact_dict["raw_data"] = contact.raw_data
            
            export_data.append(contact_dict)
        
        return export_data
    
    async def cleanup_orphaned_contacts(self) -> int:
        """
        Clean up contacts that reference non-existent import jobs.
        
        Returns:
            int: Number of orphaned contacts cleaned up
        """
        # This would require a JOIN with import_jobs table
        # For now, we'll implement a basic cleanup
        query = text("""
            DELETE FROM contact 
            WHERE import_id NOT IN (SELECT id FROM importjob)
        """)
        
        result = await self.session.execute(query)
        deleted_count = result.rowcount
        
        logger.info(f"Cleaned up {deleted_count} orphaned contacts")
        return deleted_count