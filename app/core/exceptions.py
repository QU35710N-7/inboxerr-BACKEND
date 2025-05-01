"""
Custom exception classes for Inboxerr Backend.
"""
from typing import Any, Dict, Optional


class InboxerrException(Exception):
    """Base exception class for Inboxerr application."""
    
    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class AuthenticationError(InboxerrException):
    """Raised when authentication fails."""
    
    def __init__(
        self,
        message: str = "Authentication failed",
        code: str = "AUTHENTICATION_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, status_code=401, details=details)


class AuthorizationError(InboxerrException):
    """Raised when a user doesn't have permission."""
    
    def __init__(
        self,
        message: str = "Not authorized",
        code: str = "AUTHORIZATION_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, status_code=403, details=details)


class ValidationError(InboxerrException):
    """Raised for validation errors."""
    
    def __init__(
        self,
        message: str = "Validation error",
        code: str = "VALIDATION_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, status_code=422, details=details)


class NotFoundError(InboxerrException):
    """Raised when a resource is not found."""
    
    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "NOT_FOUND",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, status_code=404, details=details)


class SMSGatewayError(InboxerrException):
    """Raised when there's an error with the SMS gateway."""
    
    def __init__(
        self,
        message: str = "SMS Gateway error",
        code: str = "SMS_GATEWAY_ERROR",
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 502,
    ):
        super().__init__(message=message, code=code, status_code=status_code, details=details)


class RetryableError(InboxerrException):
    """Error that can be retried."""
    
    def __init__(
        self,
        message: str = "Retryable error",
        code: str = "RETRYABLE_ERROR",
        details: Optional[Dict[str, Any]] = None,
        retry_after: int = 60,
    ):
        details = details or {}
        details["retry_after"] = retry_after
        super().__init__(message=message, code=code, status_code=503, details=details)


class WebhookError(InboxerrException):
    """Raised when there's an issue with webhook processing."""
    
    def __init__(
        self,
        message: str = "Webhook error",
        code: str = "WEBHOOK_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, status_code=400, details=details)


class SMSAuthError(SMSGatewayError):
    """Raised when SMS gateway credentials are invalid."""
    def __init__(
        self,
        message: str = "Invalid SMS gateway credentials",
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message=message,
            code="SMS_AUTH_ERROR",
            status_code=401,
            details=details
        )
