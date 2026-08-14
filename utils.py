"""
Utility functions for the Cozi API client.
"""

import re
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from exceptions import ValidationError

# The only shape a Cozi list/item id is allowed to take.
#
# Ids reach the API two ways, and both are injectable without this: they are
# interpolated into the request path, and into the JSON-Pointer `path` of a PATCH
# operation. A `../` in an id escapes the intended prefix -- urljoin applies RFC
# 3986 remove_dot_segments and aiohttp's yarl normalizes dot segments again -- so
# `/api/ext/2004/<acct>/list/../../../../evil` becomes rest.cozi.com/api/evil,
# aiming a request that carries the account's bearer token at an arbitrary path.
# A `?` or `#` truncates the path into a query or fragment instead. In a
# JSON-Pointer, `/` and `~` retarget the patch at a different node.
#
# Restricting ids to unreserved characters closes all of it at once, which is why
# no separate percent-encoding or RFC 6901 escaping step is needed downstream.
# Kept byte-identical to ID_RE in the parallel TypeScript client (cozi_mcp) so
# both agree on what a legal id is, and so the pattern string stays valid
# ECMAScript for anything that re-exports it (a Pydantic Field(pattern=...) that
# ends up in a JSON Schema, say). Matching is done with fullmatch below rather
# than by anchoring with \Z, which would not survive that trip.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_id(value: str, name: str = "id") -> str:
    """
    Return ``value`` unchanged if it is a well-formed Cozi id, else raise.

    Call this before any authentication or network I/O, so a malformed id costs
    no round trip.

    Args:
        value: The candidate id
        name: Parameter name, used in the error message

    Returns:
        The id, unchanged

    Raises:
        ValidationError: value is not a string matching ``ID_PATTERN``
    """
    # fullmatch, not match: Python's `$` also matches just before a trailing
    # newline, so match() would accept "goodid\n". JavaScript's `$` does not do
    # this without the `m` flag, so match() would be laxer than the TS validator
    # this ports. The isinstance guard is load-bearing too -- re.fullmatch(None)
    # raises a bare TypeError, and callers may be feeding this LLM-supplied JSON.
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValidationError(f"Invalid {name}: {value!r}")
    return value


def validate_calendar_period(year: int, month: int) -> Tuple[int, int]:
    """
    Return ``(year, month)`` as ints if they can safely form a calendar path.

    The annotations on the calendar methods are not enforced at runtime, so a
    caller passing a string puts it straight into the request path -- the same
    traversal sink :func:`validate_id` closes for list and item ids.

    Args:
        year: Four-digit year
        month: Month, 1-12

    Returns:
        (year, month) as ints

    Raises:
        ValidationError: either value is not an integer, or is out of range
    """
    validated = []
    for name, value, low, high in (
        ("year", year, 1900, 2999),
        ("month", month, 1, 12),
    ):
        # bool is an int subclass; True would otherwise pass as month 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"Invalid {name}: expected an integer, got {value!r}")
        if not low <= value <= high:
            raise ValidationError(f"Invalid {name}: {value} is not in {low}-{high}")
        validated.append(value)

    return validated[0], validated[1]


def validate_email(email: str) -> bool:
    """Validate email address format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def parse_time_string(time_str: str) -> Optional[time]:
    """
    Parse various time string formats into time object.
    
    Supports:
    - "14:30" (24-hour)
    - "2:30 PM" (12-hour)
    - "2:30PM" (12-hour without space)
    """
    if not time_str:
        return None
    
    time_str = time_str.strip()
    
    # Try 24-hour format first
    if ':' in time_str and ('AM' not in time_str.upper() and 'PM' not in time_str.upper()):
        try:
            parts = time_str.split(':')
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return time(hour=hour, minute=minute)
        except ValueError:
            pass
    
    # Try 12-hour format
    time_str_upper = time_str.upper().replace(' ', '')
    if 'AM' in time_str_upper or 'PM' in time_str_upper:
        try:
            is_pm = 'PM' in time_str_upper
            time_part = time_str_upper.replace('AM', '').replace('PM', '')
            
            if ':' in time_part:
                parts = time_part.split(':')
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            else:
                hour = int(time_part)
                minute = 0
            
            # Convert to 24-hour format
            if is_pm and hour != 12:
                hour += 12
            elif not is_pm and hour == 12:
                hour = 0
            
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return time(hour=hour, minute=minute)
        except ValueError:
            pass
    
    return None


def format_time_for_display(time_obj: Optional[time], use_12_hour: bool = True) -> str:
    """Format time object for display."""
    if not time_obj:
        return ""
    
    if use_12_hour:
        return time_obj.strftime("%I:%M %p").lstrip('0')
    else:
        return time_obj.strftime("%H:%M")


def calculate_date_span(start_date: date, end_date: date) -> int:
    """Calculate the number of days between two dates."""
    return max(1, (end_date - start_date).days + 1)


def split_long_text(text: str, max_length: int = 100) -> List[str]:
    """Split long text into chunks for better display."""
    if len(text) <= max_length:
        return [text]
    
    words = text.split()
    chunks = []
    current_chunk = []
    current_length = 0
    
    for word in words:
        if current_length + len(word) + 1 <= max_length:
            current_chunk.append(word)
            current_length += len(word) + 1
        else:
            if current_chunk:
                chunks.append(' '.join(current_chunk))
            current_chunk = [word]
            current_length = len(word)
    
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    
    return chunks


def sanitize_list_title(title: str) -> str:
    """Sanitize list title for API usage."""
    return title.strip()[:255]  # Cozi likely has a length limit


def sanitize_item_text(text: str) -> str:
    """Sanitize item text for API usage."""
    return text.strip()[:1000]  # Reasonable limit for item text


def extract_attendee_names(attendee_list: List[str], family_members: List[str]) -> Tuple[List[str], List[str]]:
    """
    Extract known family members and unknown attendees from an attendee list.
    
    Returns:
        Tuple of (known_family_members, unknown_attendees)
    """
    family_set = set(family_members)
    known = []
    unknown = []
    
    for attendee in attendee_list:
        if attendee in family_set:
            known.append(attendee)
        else:
            unknown.append(attendee)
    
    return known, unknown


def is_weekend(date_obj: date) -> bool:
    """Check if a date falls on a weekend."""
    return date_obj.weekday() >= 5  # Saturday = 5, Sunday = 6


def get_week_boundaries(date_obj: date) -> Tuple[date, date]:
    """Get the start and end dates of the week containing the given date."""
    days_since_monday = date_obj.weekday()
    week_start = date_obj - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end