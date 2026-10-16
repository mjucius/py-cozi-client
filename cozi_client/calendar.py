"""
Calendar support for the Cozi client. (Filled in during the 3.0 domain phase.)
"""

from ._transport import Transport


class CalendarMixin(Transport):
    """Calendar endpoints. Mixed into CoziClient; uses Transport._request."""
