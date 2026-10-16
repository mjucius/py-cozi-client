"""
Async Python client for the Cozi Family Organizer API.
"""

from importlib.metadata import PackageNotFoundError, version

from ._transport import ID_PATTERN, MAX_PHOTO_BYTES, validate_calendar_period, validate_id
from .account import (
    FAMILY_FEED_ID,
    HOLIDAY_CALENDARS,
    AgendaChannel,
    CoziCalendarSubscription,
    CoziHousehold,
    CoziIcalFeed,
    CoziPerson,
    CoziSharedCalendar,
    EmailStatus,
    NotificationChannel,
    PersonSettings,
    default_settings,
)
from .calendar import (
    MEAL_SLOTS,
    WHOLE_SERIES,
    CoziAppointment,
    Extent,
    Occurrence,
    Scope,
    date_span_for_last_day,
    last_day_of,
    repeat_rule,
)
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
from .lists import CoziItem, CoziList, ItemStatus, ListType
from .recipes import CoziIngredient, CoziRecipe

try:
    __version__ = version("py-cozi-client")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"

__all__ = [
    "CoziClient",
    # lists
    "CoziList",
    "CoziItem",
    "ListType",
    "ItemStatus",
    # calendar
    "CoziAppointment",
    "Occurrence",
    "Extent",
    "WHOLE_SERIES",
    "Scope",
    "repeat_rule",
    "last_day_of",
    "date_span_for_last_day",
    "MEAL_SLOTS",
    # account
    "CoziPerson",
    "PersonSettings",
    "NotificationChannel",
    "AgendaChannel",
    "EmailStatus",
    "default_settings",
    "CoziHousehold",
    "CoziIcalFeed",
    "CoziSharedCalendar",
    "CoziCalendarSubscription",
    "FAMILY_FEED_ID",
    "HOLIDAY_CALENDARS",
    # recipes
    "CoziRecipe",
    "CoziIngredient",
    # exceptions
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
    # helpers
    "MAX_PHOTO_BYTES",
    "ID_PATTERN",
    "validate_id",
    "validate_calendar_period",
]
