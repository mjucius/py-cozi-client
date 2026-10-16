"""
Async Python client for the Cozi Family Organizer API.
"""

from importlib.metadata import PackageNotFoundError, version

from ._transport import ID_PATTERN, validate_calendar_period, validate_id
from .client import CoziClient
from .exceptions import (
    APIError,
    AuthenticationError,
    ConflictError,
    CoziException,
    CoziValidationError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    ResourceNotFoundError,
    ResponseParseError,
    WriteVerificationError,
)

try:
    __version__ = version("py-cozi-client")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"

__all__ = [
    "CoziClient",
    "CoziException",
    "AuthenticationError",
    "CoziValidationError",
    "RateLimitError",
    "NetworkError",
    "APIError",
    "ResourceNotFoundError",
    "PermissionDeniedError",
    "ConflictError",
    "WriteVerificationError",
    "ResponseParseError",
    "ID_PATTERN",
    "validate_id",
    "validate_calendar_period",
]
