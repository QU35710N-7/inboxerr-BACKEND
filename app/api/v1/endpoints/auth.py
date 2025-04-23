"""
API endpoints for authentication.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Body
from fastapi.security import OAuth2PasswordRequestForm

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError, NotFoundError
from app.api.v1.dependencies import (
    get_current_user,
    get_user_repository,
    validate_permissions
)
from app.schemas.user import (
    User,
    UserCreate,
    Token,
    APIKey,
    APIKeyCreate
)
from app.core.security import (
    create_access_token,
    verify_password,
    get_password_hash
)

router = APIRouter()


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    user_repository = Depends(get_user_repository)
):
    """
    OAuth2 compatible token login, get an access token for future requests.
    """
    try:
        # Authenticate user
        user = await user_repository.get_by_email(form_data.username)
        if not user:
            raise AuthenticationError("Incorrect email or password")
        
        # Verify password
        if not verify_password(form_data.password, user.hashed_password):
            raise AuthenticationError("Incorrect email or password")
        
        # Check if user is active
        if not user.is_active:
            raise AuthenticationError("Inactive user")
        
        # Create access token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        expires_at = datetime.utcnow() + access_token_expires
        
        access_token = create_access_token(
            data={
                "sub": str(user.id),
                "role": user.role,
                "exp": expires_at
            },
            expires_delta=access_token_expires
        )
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_at": expires_at
        }
        
    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/register", response_model=User, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_data: UserCreate,
    user_repository = Depends(get_user_repository)
):
    """
    Register a new user.
    """
    # Check if user already exists
    existing_user = await user_repository.get_by_email(user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    
    user = await user_repository.create(
        email=user_data.email,
        hashed_password=hashed_password,
        full_name=user_data.full_name,
        is_active=user_data.is_active,
        role=user_data.role
    )
    
    return user


@router.get("/me", response_model=User)
async def read_users_me(
    current_user: User = Depends(get_current_user)
):
    """
    Get current user information.
    """
    return current_user


@router.post("/keys", response_model=APIKey)
async def create_api_key(
    api_key_data: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    user_repository = Depends(get_user_repository)
):
    """
    Create a new API key.
    
    This is the only time the full API key will be returned.
    """
    # Create API key
    api_key = await user_repository.create_api_key(
        user_id=current_user.id,
        name=api_key_data.name,
        expires_at=api_key_data.expires_at,
        permissions=api_key_data.permissions
    )
    
    return api_key


@router.get("/keys", response_model=List[APIKey])
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    user_repository = Depends(get_user_repository)
):
    """
    List all API keys for the current user.
    
    Note: The full API key value is not returned, only the ID and metadata.
    """
    # List API keys
    api_keys = await user_repository.list_api_keys(user_id=current_user.id)
    
    # Remove sensitive information
    for key in api_keys:
        key.key = f"{key.key[:8]}..." if key.key else None
    
    return api_keys


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    user_repository = Depends(get_user_repository)
):
    """
    Delete an API key.
    """
    # Get API key
    api_key = await user_repository.get_api_key_by_id(key_id)
    if not api_key:
        raise NotFoundError(message="API key not found")
    
    # Check ownership
    if api_key.user_id != str(current_user.id) and current_user.role != "admin":
        raise AuthorizationError(message="Not authorized to delete this API key")
    
    # Delete API key
    success = await user_repository.delete_api_key(key_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete API key"
        )
    
    return None