"""
Utilities for standardized datetime handling.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Union
import re

def utc_now() -> datetime:
    """
    Get current UTC time as timezone-aware datetime.
    
    Returns:
        datetime: Current UTC time
    """
    return datetime.now(timezone.utc)

def format_datetime(dt: Optional[datetime] = None) -> str:
    """
    Format datetime as ISO 8601 string.
    
    Args:
        dt: Datetime to format (defaults to current UTC time)
        
    Returns:
        str: ISO 8601 formatted string
    """
    if dt is None:
        dt = utc_now()
    
    # Ensure datetime is timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    return dt.isoformat()

def parse_datetime(date_string: str) -> Optional[datetime]:
    """
    Parse datetime from string.
    
    Args:
        date_string: Datetime string to parse
        
    Returns:
        datetime: Parsed datetime or None if invalid
    """
    try:
        # Handle common formats
        if 'T' in date_string:
            # ISO format
            if date_string.endswith('Z'):
                date_string = date_string[:-1] + '+00:00'
            return datetime.fromisoformat(date_string)
        else:
            # Try common date formats
            date_patterns = [
                # YYYY-MM-DD
                r'^(\d{4})-(\d{2})-(\d{2})$',
                # MM/DD/YYYY
                r'^(\d{1,2})/(\d{1,2})/(\d{4})$',
                # DD/MM/YYYY
                r'^(\d{1,2})-(\d{1,2})-(\d{4})$',
            ]
            
            for pattern in date_patterns:
                match = re.match(pattern, date_string)
                if match:
                    if pattern == date_patterns[0]:
                        year, month, day = match.groups()
                    else:
                        if pattern == date_patterns[1]:
                            month, day, year = match.groups()
                        else:
                            day, month, year = match.groups()
                    
                    return datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
            
            # If all patterns fail, try direct parsing
            return datetime.fromisoformat(date_string)
            
    except (ValueError, TypeError):
        return None

def add_time(dt: datetime, *, 
            days: int = 0, 
            hours: int = 0, 
            minutes: int = 0, 
            seconds: int = 0) -> datetime:
    """
    Add time to datetime.
    
    Args:
        dt: Base datetime
        days: Days to add
        hours: Hours to add
        minutes: Minutes to add
        seconds: Seconds to add
        
    Returns:
        datetime: New datetime
    """
    # Ensure datetime is timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    return dt + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

def subtract_time(dt: datetime, *, 
                days: int = 0, 
                hours: int = 0, 
                minutes: int = 0, 
                seconds: int = 0) -> datetime:
    """
    Subtract time from datetime.
    
    Args:
        dt: Base datetime
        days: Days to subtract
        hours: Hours to subtract
        minutes: Minutes to subtract
        seconds: Seconds to subtract
        
    Returns:
        datetime: New datetime
    """
    return add_time(dt, days=-days, hours=-hours, minutes=-minutes, seconds=-seconds)

def is_future(dt: datetime) -> bool:
    """
    Check if datetime is in the future.
    
    Args:
        dt: Datetime to check
        
    Returns:
        bool: True if datetime is in the future
    """
    # Ensure datetime is timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    return dt > utc_now()

def is_past(dt: datetime) -> bool:
    """
    Check if datetime is in the past.
    
    Args:
        dt: Datetime to check
        
    Returns:
        bool: True if datetime is in the past
    """
    # Ensure datetime is timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    return dt < utc_now()

def ensure_utc(dt: datetime) -> datetime:
    """
    Ensure datetime is UTC timezone-aware.
    
    Args:
        dt: Datetime to process
        
    Returns:
        datetime: UTC timezone-aware datetime
    """
    # If timezone-naive, assume it's already UTC and add timezone
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    
    # If it has a different timezone, convert to UTC
    return dt.astimezone(timezone.utc)