"""
Security utilities for authentication and authorization.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union
import jwt
from passlib.context import CryptContext
import secrets
import string

from app.core.config import settings

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.
    
    Args:
        plain_password: Plain-text password
        hashed_password: Hashed password
        
    Returns:
        bool: True if password matches hash
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password.
    
    Args:
        password: Plain-text password
        
    Returns:
        str: Hashed password
    """
    return pwd_context.hash(password)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT access token.
    
    Args:
        data: Data to encode in the token
        expires_delta: Token expiration time
        
    Returns:
        str: JWT token
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm="HS256"
    )
    
    return encoded_jwt


def generate_api_key() -> str:
    """
    Generate a secure API key.
    
    Returns:
        str: API key
    """
    # Characters to use in API key
    alphabet = string.ascii_letters + string.digits
    
    # Generate a random string for the API key
    api_key = ''.join(secrets.choice(alphabet) for _ in range(32))
    
    # Add prefix for identification
    return f"ibx_{''.join(secrets.choice(alphabet) for _ in range(8))}_{api_key}"


def validate_api_key(api_key: str) -> bool:
    """
    Validate API key format.
    
    Args:
        api_key: API key to validate
        
    Returns:
        bool: True if format is valid
    """
    # Check format (prefix_random_key)
    parts = api_key.split('_')
    if len(parts) != 3:
        return False
    
    prefix, random_part, key = parts
    
    # Validate prefix
    if prefix != "ibx":
        return False
    
    # Validate random part length
    if len(random_part) != 8:
        return False
    
    # Validate key length
    if len(key) != 32:
        return False
    
    # Validate characters
    valid_chars = set(string.ascii_letters + string.digits)
    return all(c in valid_chars for c in random_part + key)


def generate_webhook_signing_key() -> str:
    """
    Generate a secure webhook signing key.
    
    Returns:
        str: Webhook signing key
    """
    # Generate a random string for the signing key
    return secrets.token_hex(32)  # 64 character hex string


def create_hmac_signature(payload: str, secret_key: str, timestamp: str) -> str:
    """
    Create HMAC signature for webhook payload validation.
    
    Args:
        payload: JSON payload as string
        secret_key: Secret key for signing
        timestamp: Timestamp string
        
    Returns:
        str: HMAC signature
    """
    import hmac
    import hashlib
    
    message = (payload + timestamp).encode()
    signature = hmac.new(
        secret_key.encode(),
        message,
        hashlib.sha256
    ).hexdigest()
    
    return signature


def verify_webhook_signature(
    payload: str,
    signature: str,
    secret_key: str,
    timestamp: str,
    tolerance: int = 300
) -> bool:
    """
    Verify webhook signature.
    
    Args:
        payload: JSON payload as string
        signature: Signature to verify
        secret_key: Secret key for signing
        timestamp: Timestamp used in signature
        tolerance: Timestamp tolerance in seconds
        
    Returns:
        bool: True if signature is valid
    """
    import hmac
    import time
    
    # Verify timestamp is within tolerance
    try:
        ts = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - ts) > tolerance:
            return False
    except (ValueError, TypeError):
        return False
    
    # Calculate expected signature
    expected = create_hmac_signature(payload, secret_key, timestamp)
    
    # Compare signatures (constant-time comparison)
    return hmac.compare_digest(expected, signature)