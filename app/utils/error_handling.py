"""
Utilities for standardized error handling across API endpoints.
"""
from typing import Any, Dict, Optional, Type, Union, List
import logging
from fastapi import HTTPException, status
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import (
    InboxerrException, 
    ValidationError, 
    NotFoundError, 
    AuthenticationError,
    AuthorizationError,
    SMSGatewayError,
    RetryableError,
    WebhookError
)

logger = logging.getLogger("inboxerr.errors")

class ErrorResponse:
    """Standard error response format."""
    
    @staticmethod
    def model(
        status_code: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a standardized error response model.
        
        Args:
            status_code: HTTP status code
            code: Error code
            message: Error message
            details: Additional error details
            
        Returns:
            Dict: Standardized error response
        """
        return {
            "status": "error",
            "code": code,
            "message": message,
            "details": details or {}
        }
    
    @staticmethod
    def from_exception(exception: Union[Exception, InboxerrException]) -> Dict[str, Any]:
        """
        Create error response from exception.
        
        Args:
            exception: Exception to process
            
        Returns:
            Dict: Standardized error response
        """
        if isinstance(exception, InboxerrException):
            # Use attributes from custom exception
            return ErrorResponse.model(
                status_code=exception.status_code,
                code=exception.code,
                message=exception.message,
                details=exception.details
            )
        elif isinstance(exception, HTTPException):
            # Convert FastAPI HTTPException
            return ErrorResponse.model(
                status_code=exception.status_code,
                code=f"HTTP_{exception.status_code}",
                message=exception.detail,
                details=getattr(exception, "details", None)
            )
        elif isinstance(exception, PydanticValidationError):
            # Convert Pydantic validation error
            return ErrorResponse.model(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="VALIDATION_ERROR",
                message="Validation error",
                details={"errors": exception.errors()}
            )
        else:
            # Generic exception
            return ErrorResponse.model(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="INTERNAL_ERROR",
                message=str(exception),
                details={"type": type(exception).__name__}
            )


def handle_exception(exception: Exception) -> HTTPException:
    """
    Convert any exception to appropriate HTTPException.
    
    Args:
        exception: Exception to handle
        
    Returns:
        HTTPException: FastAPI HTTP exception
    """
    # Log all exceptions
    if isinstance(exception, (ValidationError, NotFoundError)):
        logger.info(f"Expected exception: {exception}")
    else:
        logger.error(f"Exception: {exception}", exc_info=True)
    
    # Map custom exceptions to status codes
    if isinstance(exception, ValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif isinstance(exception, NotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exception, AuthenticationError):
        status_code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(exception, AuthorizationError):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exception, SMSGatewayError):
        status_code = status.HTTP_502_BAD_GATEWAY
    elif isinstance(exception, RetryableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(exception, WebhookError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exception, PydanticValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif isinstance(exception, HTTPException):
        # Already a FastAPI HTTPException, just return it
        return exception
    else:
        # Default to internal server error
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    
    # Get error response
    error_response = ErrorResponse.from_exception(exception)
    
    # Create FastAPI HTTPException
    http_exception = HTTPException(
        status_code=status_code,
        detail=error_response
    )
    
    # Add authentication headers if needed
    if isinstance(exception, AuthenticationError):
        http_exception.headers = {"WWW-Authenticate": "Bearer"}
    
    # Add retry headers if needed
    if isinstance(exception, RetryableError):
        retry_after = getattr(exception, "details", {}).get("retry_after", 60)
        http_exception.headers = {"Retry-After": str(retry_after)}
    
    return http_exception


def validation_error(message: str, details: Optional[Dict[str, Any]] = None) -> HTTPException:
    """
    Create a validation error response.
    
    Args:
        message: Error message
        details: Additional error details
        
    Returns:
        HTTPException: FastAPI HTTP exception
    """
    return handle_exception(ValidationError(message=message, details=details))


def not_found_error(message: str, details: Optional[Dict[str, Any]] = None) -> HTTPException:
    """
    Create a not found error response.
    
    Args:
        message: Error message
        details: Additional error details
        
    Returns:
        HTTPException: FastAPI HTTP exception
    """
    return handle_exception(NotFoundError(message=message, details=details))


def auth_error(message: str, details: Optional[Dict[str, Any]] = None) -> HTTPException:
    """
    Create an authentication error response.
    
    Args:
        message: Error message
        details: Additional error details
        
    Returns:
        HTTPException: FastAPI HTTP exception
    """
    return handle_exception(AuthenticationError(message=message, details=details))


def permission_error(message: str, details: Optional[Dict[str, Any]] = None) -> HTTPException:
    """
    Create an authorization error response.
    
    Args:
        message: Error message
        details: Additional error details
        
    Returns:
        HTTPException: FastAPI HTTP exception
    """
    return handle_exception(AuthorizationError(message=message, details=details))


def server_error(message: str, details: Optional[Dict[str, Any]] = None) -> HTTPException:
    """
    Create a server error response.
    
    Args:
        message: Error message
        details: Additional error details
        
    Returns:
        HTTPException: FastAPI HTTP exception
    """
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=ErrorResponse.model(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message=message,
            details=details
        )
    )