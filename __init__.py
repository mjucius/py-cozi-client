"""
Cozi Family Organizer API Client

A Python client for the Cozi Family Organizer REST API.
This client provides a robust and type-safe interface to the Cozi service
with Pydantic models for automatic validation and serialization.
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
    NetworkError,
    ResourceNotFoundError,
    PermissionDeniedError,
    ValidationError,
    WriteVerificationError,
)
from utils import ID_PATTERN, validate_calendar_period, validate_id

__version__ = "2.0.4"
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
    "NetworkError",
    "ResourceNotFoundError",
    "PermissionDeniedError",
    "ValidationError",
    "WriteVerificationError",
    "ID_PATTERN",
    "validate_id",
    "validate_calendar_period",
]