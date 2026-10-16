"""
Exceptions raised by the Cozi client. Every error the client raises is a CoziException.
"""

from typing import Any, Dict, Optional


class CoziException(Exception):
    """Base class for all Cozi errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class AuthenticationError(CoziException):
    """Login, token renewal or an authenticated request was refused."""


class CoziValidationError(CoziException):
    """An argument was rejected before any request was sent."""


class RateLimitError(CoziException):
    """Cozi kept answering 429 after the retries ran out."""


class NetworkError(CoziException):
    """The request never got an HTTP answer (connection error or timeout)."""


class APIError(CoziException):
    """Cozi answered with an error status, or with a body that cannot be used."""


class ResourceNotFoundError(APIError):
    """HTTP 404, or an id that turned out not to exist."""


class PermissionDeniedError(APIError):
    """HTTP 402/403. Some features (search, reminders, smart add...) need a paid Cozi plan."""


class ConflictError(APIError):
    """
    HTTP 409/410: someone else changed or deleted the data since it was read.

    The web client tells the user to refresh and try again.
    """


class WriteVerificationError(APIError):
    """
    A write returned a success status but the server did not apply it.

    Cozi's calendar endpoint answers 200 even when it discards an operation,
    reporting the reason only in a ``rejectedItems`` array in the response body.
    Without an explicit check a discarded write is indistinguishable from a
    successful one, so the caller reports success that never happened.
    """


class ResponseParseError(APIError):
    """A success response was not JSON, or did not have the shape the client expects."""
