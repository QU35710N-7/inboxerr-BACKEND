"""
Base repository with common database operations.
"""
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

# Define generic types for models
ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class BaseRepository(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    Base repository with common CRUD operations.
    
    Generic repository pattern implementation for database access.
    """
    
    def __init__(self, session: AsyncSession, model: Type[ModelType]):
        """
        Initialize repository with session and model.
        
        Args:
            session: Database session
            model: SQLAlchemy model class
        """
        self.session = session
        self.model = model
        self.session_is_owned = False
    
    async def __aenter__(self):
        """Support async context manager protocol."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up resources when exiting context."""
        if self._session_is_owned:
            await self.close()
    
    async def close(self):
        """Close the session if we own it."""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def get_by_id(self, id: str) -> Optional[ModelType]:
        """
        Get a record by ID.
        
        Args:
            id: Record ID
            
        Returns:
            ModelType: Found record or None
        """
        query = select(self.model).where(self.model.id == id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_by_attribute(self, attr_name: str, attr_value: Any) -> Optional[ModelType]:
        """
        Get a record by a specific attribute.
        
        Args:
            attr_name: Attribute name
            attr_value: Attribute value
            
        Returns:
            ModelType: Found record or None
        """
        query = select(self.model).where(getattr(self.model, attr_name) == attr_value)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def list(
        self, 
        *,
        filters: Optional[Dict[str, Any]] = None,
        skip: int = 0, 
        limit: int = 100
    ) -> List[ModelType]:
        """
        Get a list of records with optional filtering.
        
        Args:
            filters: Optional filters as dict
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            List[ModelType]: List of records
        """
        query = select(self.model)
        
        # Apply filters if provided
        if filters:
            for attr_name, attr_value in filters.items():
                if hasattr(self.model, attr_name) and attr_value is not None:
                    query = query.where(getattr(self.model, attr_name) == attr_value)
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def create(self, *, obj_in: Union[CreateSchemaType, Dict[str, Any]]) -> ModelType:
        """
        Create a new record.
        
        Args:
            obj_in: Data to create record with
            
        Returns:
            ModelType: Created record
        """
        # Convert to dict if it's a Pydantic model
        obj_in_data = obj_in if isinstance(obj_in, dict) else obj_in.dict(exclude_unset=True)
        
        # Create model instance
        db_obj = self.model(**obj_in_data)
        
        # Generate ID if not provided
        if not db_obj.id:
            db_obj.id = str(uuid4())
        
        # Add to session
        self.session.add(db_obj)
        await self.session.commit()
        await self.session.refresh(db_obj)
        
        return db_obj
    
    async def update(
        self, 
        *,
        id: str,
        obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> Optional[ModelType]:
        """
        Update a record.
        
        Args:
            id: Record ID
            obj_in: Data to update record with
            
        Returns:
            ModelType: Updated record or None
        """
        # Get current record
        db_obj = await self.get_by_id(id)
        if not db_obj:
            return None
        
        # Convert to dict if it's a Pydantic model
        update_data = obj_in if isinstance(obj_in, dict) else obj_in.dict(exclude_unset=True)
        
        # Remove None values
        update_data = {k: v for k, v in update_data.items() if v is not None}
        
        # Update record
        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        
        # Save changes
        self.session.add(db_obj)
        await self.session.commit()
        await self.session.refresh(db_obj)
        
        return db_obj
    
    async def delete(self, *, id: str) -> bool:
        """
        Delete a record.
        
        Args:
            id: Record ID
            
        Returns:
            bool: True if deleted, False if not found
        """
        # Check if record exists
        db_obj = await self.get_by_id(id)
        if not db_obj:
            return False
        
        # Delete record
        await self.session.delete(db_obj)
        await self.session.commit()
        
        return True
    
    async def count(self, *, filters: Optional[Dict[str, Any]] = None) -> int:
        """
        Count records with optional filtering.
        
        Args:
            filters: Optional filters as dict
            
        Returns:
            int: Number of records
        """
        from sqlalchemy import func
        
        query = select(func.count()).select_from(self.model)
        
        # Apply filters if provided
        if filters:
            for attr_name, attr_value in filters.items():
                if hasattr(self.model, attr_name) and attr_value is not None:
                    query = query.where(getattr(self.model, attr_name) == attr_value)
        
        result = await self.session.execute(query)
        return result.scalar_one()
    

    async def execute_in_transaction(self, func, *args, **kwargs):
        """
        Execute a function within a transaction.
        
        Args:
            func: Async function to execute
            args: Function positional arguments
            kwargs: Function keyword arguments
            
        Returns:
            The result of the function
        """
        async with self.session.begin():
            return await func(*args, **kwargs)