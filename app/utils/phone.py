"""
Phone number validation and formatting utilities.
"""
import re
from typing import Any, Tuple, Dict, Optional, List
import logging

logger = logging.getLogger("inboxerr.phone")

try:
    import phonenumbers
    from phonenumbers import NumberParseException, PhoneNumberFormat
    PHONENUMBERS_AVAILABLE = True
except ImportError:
    PHONENUMBERS_AVAILABLE = False
    logger.warning("phonenumbers library not available, using basic validation")


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


def validate_phone_advanced(number: str) -> Tuple[bool, str, Optional[str], Optional[Dict[str, Any]]]:
    """
    Advanced phone number validation using the phonenumbers library.
    
    Args:
        number: Phone number to validate
        
    Returns:
        Tuple[bool, str, str, dict]: (is_valid, formatted_number, error_message, metadata)
    """
    metadata = {}
    
    try:
        # Parse the phone number
        try:
            parsed = phonenumbers.parse(number, None)
        except NumberParseException as e:
            return False, number, f"Parse error: {str(e)}", None
        
        # Check if it's a valid number
        if not phonenumbers.is_valid_number(parsed):
            return False, number, "Invalid phone number", None
        
        # Format in E.164 format
        formatted = phonenumbers.format_number(
            parsed, PhoneNumberFormat.E164
        )
        
        # Get the country and carrier
        country = phonenumbers.region_code_for_number(parsed)
        metadata["country"] = country
        
        # Check if it's a mobile number
        number_type = phonenumbers.number_type(parsed)
        is_mobile = (number_type == phonenumbers.PhoneNumberType.MOBILE)
        metadata["is_mobile"] = is_mobile
        
        # Check for other properties
        metadata["number_type"] = str(number_type)
        metadata["country_code"] = parsed.country_code
        metadata["national_number"] = parsed.national_number
        
        # Additional validations
        is_possible = phonenumbers.is_possible_number(parsed)
        if not is_possible:
            return False, formatted, "Number is not possible", metadata
        
        return True, formatted, None, metadata
    except Exception as e:
        return False, number, f"Validation error: {str(e)}", None


def validate_phone(number: str, strict: bool = False) -> Tuple[bool, str, Optional[str], Optional[Dict]]:
    """
    Validate and format a phone number.
    
    Uses the phonenumbers library if available, otherwise falls back to basic validation.
    
    Args:
        number: Phone number to validate
        strict: Whether to apply strict validation (country code check, etc.)
        
    Returns:
        Tuple[bool, str, str, dict]: (is_valid, formatted_number, error_message, metadata)
    """
    if PHONENUMBERS_AVAILABLE:
        return validate_phone_advanced(number)
    else:
        is_valid, formatted, error = validate_phone_basic(number)
        return is_valid, formatted, error, None


def is_valid_phone(number: str, strict: bool = False) -> bool:
    """
    Check if a phone number is valid.
    
    Args:
        number: Phone number to validate
        strict: Whether to apply strict validation
        
    Returns:
        bool: True if valid, False otherwise
    """
    is_valid, _, _, _ = validate_phone(number, strict)
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
    is_valid, formatted, error, _ = validate_phone(number)
    if not is_valid:
        raise PhoneValidationError(error or "Invalid phone number", {"number": number})
    return formatted


def validate_batch_phone_numbers(phone_numbers: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Validate a batch of phone numbers.
    
    Args:
        phone_numbers: List of phone numbers to validate
        
    Returns:
        Dict: Dictionary with 'valid' and 'invalid' lists
    """
    valid = []
    invalid = []
    
    for number in phone_numbers:
        is_valid, formatted, error, metadata = validate_phone(number)
        if is_valid:
            valid.append({
                "original": number,
                "formatted": formatted,
                "metadata": metadata or {}
            })
        else:
            invalid.append({
                "original": number,
                "error": error,
                "metadata": metadata or {}
            })
    
    return {
        "valid": valid,
        "invalid": invalid,
        "summary": {
            "total": len(phone_numbers),
            "valid_count": len(valid),
            "invalid_count": len(invalid)
        }
    }


def extract_phone_numbers(text: str) -> List[str]:
    """
    Extract potential phone numbers from text.
    
    Args:
        text: Text to extract phone numbers from
        
    Returns:
        List[str]: List of potential phone numbers
    """
    # Define regex patterns for phone number detection
    patterns = [
        r'\+\d{1,3}\s?\d{1,14}',  # +1 123456789
        r'\(\d{1,4}\)\s?\d{1,14}', # (123) 456789
        r'\d{1,4}[- .]\d{1,4}[- .]\d{1,10}'  # 123-456-7890
    ]
    
    results = []
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        results.extend(matches)
    
    # Deduplicate and return
    return list(set(results))