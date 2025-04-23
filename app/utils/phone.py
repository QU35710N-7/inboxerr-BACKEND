"""
Phone number validation and formatting utilities.
"""
import re
from typing import Tuple, Dict, Optional

try:
    import phonenumbers
    PHONENUMBERS_AVAILABLE = True
except ImportError:
    PHONENUMBERS_AVAILABLE = False


class PhoneValidationError(Exception):
    """Exception raised for phone validation errors."""
    
    def __init__(self, message: str, details: Optional[Dict] = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


def validate_phone_basic(number: str) -> Tuple[bool, str, Optional[str]]:
    """
    Basic phone number validation without external libraries.
    
    Args:
        number: Phone number to validate
        
    Returns:
        Tuple[bool, str, str]: (is_valid, formatted_number, error_message)
    """
    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)\.]+', '', number)
    
    # Check if it's just digits and maybe a leading +
    if not re.match(r'^\+?\d+$', cleaned):
        return False, number, "Phone number contains invalid characters"
    
    # Ensure it starts with + for E.164 format
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    
    # Basic length check
    if len(cleaned) < 8:
        return False, cleaned, "Phone number too short"
    if len(cleaned) > 16:
        return False, cleaned, "Phone number too long"
    
    return True, cleaned, None


def validate_phone_advanced(number: str) -> Tuple[bool, str, Optional[str]]:
    """
    Advanced phone number validation using the phonenumbers library.
    
    Args:
        number: Phone number to validate
        
    Returns:
        Tuple[bool, str, str]: (is_valid, formatted_number, error_message)
    """
    try:
        # Parse the phone number
        parsed = phonenumbers.parse(number, None)
        
        # Check if it's a valid number
        if not phonenumbers.is_valid_number(parsed):
            return False, number, "Invalid phone number"
        
        # Format in E.164 format
        formatted = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
        
        # Get the country and carrier
        country = phonenumbers.region_code_for_number(parsed)
        
        return True, formatted, None
    except phonenumbers.NumberParseException as e:
        return False, number, f"Parse error: {str(e)}"


def validate_phone(number: str) -> Tuple[bool, str, Optional[str]]:
    """
    Validate and format a phone number.
    
    Uses the phonenumbers library if available, otherwise falls back to basic validation.
    
    Args:
        number: Phone number to validate
        
    Returns:
        Tuple[bool, str, str]: (is_valid, formatted_number, error_message)
    """
    if PHONENUMBERS_AVAILABLE:
        return validate_phone_advanced(number)
    else:
        return validate_phone_basic(number)


def is_valid_phone(number: str) -> bool:
    """
    Check if a phone number is valid.
    
    Args:
        number: Phone number to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    is_valid, _, _ = validate_phone(number)
    return is_valid


def format_phone(number: str) -> str:
    """
    Format a phone number in E.164 format.
    
    Args:
        number: Phone number to format
        
    Returns:
        str: Formatted phone number or original if invalid
        
    Raises:
        PhoneValidationError: If the phone number is invalid
    """
    is_valid, formatted, error = validate_phone(number)
    if not is_valid:
        raise PhoneValidationError(error or "Invalid phone number", {"number": number})
    return formatted