"""
Custom exceptions for the Cozi API client.
"""

from typing import Optional, Dict, Any


class CoziException(Exception):
    """Base exception class for all Cozi-related errors."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class AuthenticationError(CoziException):
    """Raised when authentication fails or token expires."""
    pass


class ValidationError(CoziException):
    """Raised when request data validation fails."""
    pass


class RateLimitError(CoziException):
    """Raised when API rate limits are exceeded."""
    pass


class APIError(CoziException):
    """Raised when the API returns an error response."""
    pass


class NetworkError(CoziException):
    """Raised when network connectivity issues occur."""
    pass


class ResourceNotFoundError(APIError):
    """Raised when a requested resource is not found."""
    pass


class PermissionError(APIError):
    """Raised when access to a resource is denied."""
    pass