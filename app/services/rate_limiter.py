"""
Rate limiting service for API request throttling.
"""
import asyncio
import time
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger("inboxerr.rate_limiter")

class RateLimiter:
    """
    Service for enforcing rate limits on API requests.
    
    Uses a simple in-memory storage for tracking request counts.
    For production, consider using Redis or another distributed storage.
    """
    
    def __init__(self):
        """Initialize the rate limiter with default limits."""
        self._requests = {}
        self._lock = asyncio.Lock()
        
        # Default rate limits by operation type
        self._rate_limits = {
            "send_message": {"requests": 60, "period": 60},  # 60 requests per minute
            "send_batch": {"requests": 10, "period": 60},    # 10 batch requests per minute
            "import_messages": {"requests": 5, "period": 300},  # 5 imports per 5 minutes
            "default": {"requests": 100, "period": 60},      # Default: 100 requests per minute
        }
    
    async def check_rate_limit(
        self, 
        user_id: str, 
        operation: str = "default"
    ) -> bool:
        """
        Check if a request is within rate limits.
        
        Args:
            user_id: ID of the user making the request
            operation: Type of operation being performed
            
        Returns:
            bool: True if request is allowed, raises exception otherwise
            
        Raises:
            HTTPException: If rate limit is exceeded
        """
        from fastapi import HTTPException, status
        
        # Get rate limit for operation
        limit = self._rate_limits.get(operation, self._rate_limits["default"])
        
        # Create key for this user and operation
        key = f"{user_id}:{operation}"
        
        current_time = time.time()
        
        async with self._lock:
            # Initialize if not exists
            if key not in self._requests:
                self._requests[key] = {"count": 0, "reset_at": current_time + limit["period"]}
            
            # Check if we need to reset the counter
            if current_time > self._requests[key]["reset_at"]:
                self._requests[key] = {"count": 0, "reset_at": current_time + limit["period"]}
            
            # Check if we're over the limit
            if self._requests[key]["count"] >= limit["requests"]:
                reset_in = int(self._requests[key]["reset_at"] - current_time)
                logger.warning(f"Rate limit exceeded for {key}. Reset in {reset_in} seconds.")
                
                # Calculate when the rate limit will reset
                reset_at = datetime.fromtimestamp(self._requests[key]["reset_at"])
                
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Try again in {reset_in} seconds.",
                    headers={"Retry-After": str(reset_in)}
                )
            
            # Increment counter
            self._requests[key]["count"] += 1
            
            logger.debug(f"Rate limit for {key}: {self._requests[key]['count']}/{limit['requests']}")
            
            return True
    
    def set_limit(self, operation: str, requests: int, period: int) -> None:
        """
        Set a custom rate limit for an operation.
        
        Args:
            operation: Operation type to set limit for
            requests: Maximum number of requests allowed
            period: Time period in seconds
        """
        self._rate_limits[operation] = {"requests": requests, "period": period}
    
    async def get_limit_status(self, user_id: str, operation: str = "default") -> Dict[str, Any]:
        """
        Get current rate limit status for a user and operation.
        
        Args:
            user_id: User ID
            operation: Operation type
            
        Returns:
            Dict: Rate limit status information
        """
        # Get rate limit for operation
        limit = self._rate_limits.get(operation, self._rate_limits["default"])
        
        # Create key for this user and operation
        key = f"{user_id}:{operation}"
        
        current_time = time.time()
        
        async with self._lock:
            # Handle case where user hasn't made any requests yet
            if key not in self._requests:
                return {
                    "limit": limit["requests"],
                    "remaining": limit["requests"],
                    "reset": int(current_time + limit["period"]),
                    "used": 0
                }
            
            # Reset counter if needed
            if current_time > self._requests[key]["reset_at"]:
                self._requests[key] = {"count": 0, "reset_at": current_time + limit["period"]}
            
            # Return current status
            return {
                "limit": limit["requests"],
                "remaining": max(0, limit["requests"] - self._requests[key]["count"]),
                "reset": int(self._requests[key]["reset_at"]),
                "used": self._requests[key]["count"]
            }


# Singleton instance for dependency injection
_rate_limiter = RateLimiter()

def get_rate_limiter() -> RateLimiter:
    """Get the singleton rate limiter instance."""
    return _rate_limiter