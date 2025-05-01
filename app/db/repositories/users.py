"""
User repository for database operations related to users.
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from app.utils.ids import generate_prefixed_id, IDPrefix
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash, generate_api_key
from app.db.repositories.base import BaseRepository
from app.models.user import User, APIKey
from app.schemas.user import UserCreate, UserUpdate


class UserRepository(BaseRepository[User, UserCreate, UserUpdate]):
    """User repository for database operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize with session and User model."""
        super().__init__(session=session, model=User)
    
    async def get_by_email(self, email: str) -> Optional[User]:
        """
        Get a user by email.
        
        Args:
            email: User email
            
        Returns:
            User: Found user or None
        """
        return await self.get_by_attribute("email", email)
    
    async def create(
        self, 
        *,
        email: str,
        hashed_password: str,
        full_name: Optional[str] = None,
        is_active: bool = True,
        role: str = "user"
    ) -> User:
        """
        Create a new user.
        
        Args:
            email: User email
            hashed_password: Hashed password
            full_name: User's full name
            is_active: Whether the user is active
            role: User role
            
        Returns:
            User: Created user
        """
        db_obj = User(
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            is_active=is_active,
            role=role
        )
        
        self.session.add(db_obj)
        await self.session.commit()
        await self.session.refresh(db_obj)
        
        return db_obj
    
    async def update_password(self, *, user_id: str, new_password: str) -> Optional[User]:
        """
        Update user password.
        
        Args:
            user_id: User ID
            new_password: New password (plain text)
            
        Returns:
            User: Updated user or None
        """
        # Hash the new password
        hashed_password = get_password_hash(new_password)
        
        # Update the user
        return await self.update(
            id=user_id,
            obj_in={"hashed_password": hashed_password}
        )
    
    async def create_api_key(
        self, 
        *,
        user_id: str,
        name: str,
        expires_at: Optional[datetime] = None,
        permissions: List[str] = None
    ) -> APIKey:
        """
        Create a new API key for a user.
        
        Args:
            user_id: User ID
            name: API key name
            expires_at: Expiration timestamp
            permissions: List of permissions
            
        Returns:
            APIKey: Created API key
        """
        # Generate API key
        key_value = generate_api_key()
        
        # Create API key
        api_key = APIKey(
            id=str(uuid4()),
            key=key_value,
            name=name,
            user_id=user_id,
            expires_at=expires_at,
            is_active=True,
            permissions=permissions or []
        )
        
        self.session.add(api_key)
        await self.session.commit()
        await self.session.refresh(api_key)
        
        return api_key
    
    async def get_api_key(self, key: str) -> Optional[APIKey]:
        """
        Get an API key by its value.
        
        Args:
            key: API key value
            
        Returns:
            APIKey: Found API key or None
        """
        query = select(APIKey).where(APIKey.key == key, APIKey.is_active == True)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_api_key_by_id(self, key_id: str) -> Optional[APIKey]:
        """
        Get an API key by its ID.
        
        Args:
            key_id: API key ID
            
        Returns:
            APIKey: Found API key or None
        """
        query = select(APIKey).where(APIKey.id == key_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def list_api_keys(self, user_id: str) -> List[APIKey]:
        """
        List all API keys for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List[APIKey]: List of API keys
        """
        query = select(APIKey).where(APIKey.user_id == user_id)
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def update_api_key_usage(self, key: str) -> bool:
        """
        Update the last_used_at timestamp for an API key.
        
        Args:
            key: API key value
            
        Returns:
            bool: True if updated, False if not found
        """
        query = update(APIKey).where(
            APIKey.key == key, 
            APIKey.is_active == True
        ).values(
            last_used_at=datetime.now(timezone.utc)
        )
        
        result = await self.session.execute(query)
        await self.session.commit()
        
        return result.rowcount > 0
    
    async def delete_api_key(self, key_id: str) -> bool:
        """
        Delete an API key.
        
        Args:
            key_id: API key ID
            
        Returns:
            bool: True if deleted, False if not found
        """
        api_key = await self.get_api_key_by_id(key_id)
        if not api_key:
            return False
            
        await self.session.delete(api_key)
        await self.session.commit()
        
        return True
    
    async def deactivate_api_key(self, key_id: str) -> bool:
        """
        Deactivate an API key without deleting it.
        
        Args:
            key_id: API key ID
            
        Returns:
            bool: True if deactivated, False if not found
        """
        api_key = await self.get_api_key_by_id(key_id)
        if not api_key:
            return False
            
        api_key.is_active = False
        self.session.add(api_key)
        await self.session.commit()
        
        return True