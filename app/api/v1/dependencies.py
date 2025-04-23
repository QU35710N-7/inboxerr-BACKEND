"""
Dependencies for API endpoints.
"""
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from typing import Optional, List
import jwt
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.schemas.user import User, TokenData, UserRole
from app.db.repositories.users import UserRepository
from app.services.rate_limiter import RateLimiter

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/token")


async def get_user_repository():
    """Get user repository."""
    from app.db.session import get_repository
    return await get_repository(UserRepository)


async def get_rate_limiter():
    """Get rate limiter service."""
    return RateLimiter()


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    user_repository = Depends(get_user_repository)
) -> User:
    """
    Get the current authenticated user from the JWT token.
    
    Args:
        token: JWT token from Authorization header
        user_repository: User repository for database access
        
    Returns:
        User: The authenticated user
        
    Raises:
        AuthenticationError: If the token is invalid or expired
    """
    try:
        # Decode JWT token
        payload = jwt.decode(
            token, 
            settings.SECRET_KEY, 
            algorithms=["HS256"]
        )
        token_data = TokenData(**payload)
        
        # Check if token is expired
        if token_data.exp and datetime.utcnow() > token_data.exp:
            raise AuthenticationError("Token has expired")
        
        # Get user from database
        user = await user_repository.get_by_id(token_data.sub)
        if not user:
            raise AuthenticationError("User not found")
        
        # Check if user is active
        if not user.is_active:
            raise AuthenticationError("User is inactive")
        
        return user
        
    except jwt.PyJWTError:
        raise AuthenticationError("Invalid token")


async def verify_api_key(
    api_key: str = Header(..., alias=settings.API_KEY_HEADER),
    user_repository = Depends(get_user_repository)
) -> User:
    """
    Verify API key and return the associated user.
    
    Args:
        api_key: API key from header
        user_repository: User repository for database access
        
    Returns:
        User: The authenticated user
        
    Raises:
        AuthenticationError: If the API key is invalid
    """
    try:
        # Get API key from database
        api_key_record = await user_repository.get_api_key(api_key)
        if not api_key_record:
            raise AuthenticationError("Invalid API key")
        
        # Check if API key is active
        if not api_key_record.is_active:
            raise AuthenticationError("API key is inactive")
        
        # Check if API key is expired
        if api_key_record.expires_at and datetime.utcnow() > api_key_record.expires_at:
            raise AuthenticationError("API key has expired")
        
        # Get user associated with API key
        user = await user_repository.get_by_id(api_key_record.user_id)
        if not user:
            raise AuthenticationError("User not found")
        
        # Check if user is active
        if not user.is_active:
            raise AuthenticationError("User is inactive")
        
        # Update last used timestamp
        await user_repository.update_api_key_usage(api_key)
        
        return user
        
    except Exception as e:
        if isinstance(e, AuthenticationError):
            raise
        raise AuthenticationError("API key verification failed")


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Get the current active user.
    
    Args:
        current_user: Current authenticated user
        
    Returns:
        User: The authenticated active user
        
    Raises:
        AuthenticationError: If the user is inactive
    """
    if not current_user.is_active:
        raise AuthenticationError("Inactive user")
    return current_user


async def validate_permissions(
    required_permissions: List[str],
    current_user: User = Depends(get_current_user)
) -> None:
    """
    Validate that the current user has the required permissions.
    
    Args:
        required_permissions: List of required permissions
        current_user: Current authenticated user
        
    Raises:
        AuthorizationError: If the user doesn't have the required permissions
    """
    # Admin role has all permissions
    if current_user.role == UserRole.ADMIN:
        return
    
    # TODO: Implement proper permission checking
    # For now, just verify role-based access
    if current_user.role != UserRole.API and "api" in required_permissions:
        raise AuthorizationError("Insufficient permissions")