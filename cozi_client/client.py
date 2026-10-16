"""
The Cozi client: one class combining the transport with every API area.
"""

from .account import AccountMixin
from .calendar import CalendarMixin
from .lists import ListsMixin
from .recipes import RecipesMixin


class CoziClient(ListsMixin, CalendarMixin, AccountMixin, RecipesMixin):
    """
    Async client for the Cozi Family Organizer API, as used by my.cozi.com.

    Use it as an async context manager; it logs in on first use::

        async with CoziClient(email, password) as cozi:
            lists = await cozi.get_lists()

    Areas: lists and items, calendar (appointments, birthdays, meals, search),
    account (family members, household, settings, subscribed calendars), and
    recipes. Every error raised is a ``CoziException``.
    """
