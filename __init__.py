"""
Cozi Family Organizer API Client

A Python client for the Cozi Family Organizer REST API.
This client provides a robust and type-safe
interface to the Cozi service.
"""

from cozi_client import CoziClient
from models import (
    ListType,
    ItemStatus,
    CoziList,
    CoziItem,
    CoziAppointment,
    CoziPerson,
)
from exceptions import (
    CoziException,
    AuthenticationError,
    RateLimitError,
    APIError,
    ValidationError,
)

__version__ = "1.1.0"
__all__ = [
    "CoziClient",
    "ListType",
    "ItemStatus",
    "CoziList",
    "CoziItem",
    "CoziAppointment",
    "CoziPerson",
    "CoziException",
    "AuthenticationError",
    "RateLimitError",
    "APIError",
    "ValidationError",
]