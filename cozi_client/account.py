"""
Account support: family members, the household, account config, iCal export feeds
and subscribed (shared, holiday, internet) calendars.

Wire shapes follow the Settings page of my.cozi.com (bundle main.c43180f9, chunk
6035); the person and household calls were also checked live. Nothing here logs:
pii, subscription data and feed URLs must never reach a log.
"""

import os
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, TypeVar, Union

import aiohttp
from pydantic import BaseModel, ConfigDict, Field

from ._transport import JSON_CONTENT_TYPE, MAX_PHOTO_BYTES, Transport, parse_model, parse_models, validate_id
from .exceptions import CoziValidationError, ResourceNotFoundError, ResponseParseError

# The iCal feed id of the whole family (the per-person feeds use person ids).
FAMILY_FEED_ID = "00000000-0000-0000-0000-000000000000"

# Cozi's built-in subscribable calendars (chunk 6035 constant Y).
HOLIDAY_CALENDARS = {
    "US": "0ff00c00f4106e69fe473d8941238e971025b7cb",
    "CA": "19f10fd88884fc0be98b9948a2a755500f9d0b03",
    "UK": "6626b6be57cb455dd09163fdb6739d78d7923cf1",
    "AU": "7862e47d7deda10c6c738efffc78f5a962d1c25f",
    "NEWS": "2310831940b8c6ce4943241679e2cbdb1724822e",
}

PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

PERSONS = "/2004/{acct}/account/person/"
HOUSEHOLD = "/2004/{acct}/account/"
SHARED_CALENDARS = "/calendar/sharedcalendar/{acct}/"  # unversioned base (/api)

T = TypeVar("T")


class EmailStatus(str, Enum):
    """A member's email verification state. Values Cozi adds later stay plain strings."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    PENDING = "pending"
    # Not in the bundle's enum: what Cozi stores for a member without an email (live).
    NOT_VERIFIED = "not verified"


class NotificationChannel(BaseModel):
    """Email and push flags for one kind of calendar message."""

    # extra="allow": flags the model does not know (Cozi stores sendByDefault on
    # calendarItemNotification) are sent back as read on update_person.
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    email_enabled: bool = Field(False, alias="emailEnabled")
    push_enabled: bool = Field(False, alias="pushEnabled")


class AgendaChannel(BaseModel):
    """The daily or weekly agenda email."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    email_enabled: bool = Field(False, alias="emailEnabled")


class PersonSettings(BaseModel):
    """What Cozi sends a member. Missing channels default to off, so partial settings parse."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # model_validate({}) rather than the class: mypy without the pydantic plugin
    # treats aliased fields as required constructor arguments.
    calendar_item_notification: NotificationChannel = Field(
        default_factory=lambda: NotificationChannel.model_validate({}),
        alias="calendarItemNotification",
    )
    calendar_item_reminder: NotificationChannel = Field(
        default_factory=lambda: NotificationChannel.model_validate({}),
        alias="calendarItemReminder",
    )
    daily_agenda: AgendaChannel = Field(
        default_factory=lambda: AgendaChannel.model_validate({}), alias="dailyAgenda"
    )
    weekly_agenda: AgendaChannel = Field(
        default_factory=lambda: AgendaChannel.model_validate({}), alias="weeklyAgenda"
    )

    @property
    def any_email(self) -> bool:
        """True when some channel emails the member; the web then requires an email address."""
        return any(
            c.email_enabled
            for c in (
                self.calendar_item_notification,
                self.calendar_item_reminder,
                self.daily_agenda,
                self.weekly_agenda,
            )
        )


def default_settings(email: str = "") -> PersonSettings:
    """
    The web's settings for a new member: everything on except the daily agenda.

    Without an email the web turns every email channel off (its own validation
    refuses email channels with no address) and keeps push on.
    """
    by_email = bool(email)
    return PersonSettings.model_validate(
        {
            "calendarItemNotification": {"emailEnabled": by_email, "pushEnabled": True},
            "calendarItemReminder": {"emailEnabled": by_email, "pushEnabled": True},
            "dailyAgenda": {"emailEnabled": False},
            "weeklyAgenda": {"emailEnabled": by_email},
        }
    )


class CoziPerson(BaseModel):
    """
    A family member.

    ``phone``, ``is_adult``, ``account_person_type``, ``account_creator``,
    ``notifiable``, ``version`` and ``notifiable_features`` are still on the wire but
    the web client never reads them; they are kept for 2.x callers.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str = Field(alias="accountPersonId")
    name: str = ""
    email: Optional[str] = None
    color: Optional[int] = Field(None, alias="colorIndex")
    # left_to_right: a known value becomes the enum, an unknown one stays a str.
    email_status: Union[EmailStatus, str, None] = Field(
        None, alias="emailStatus", union_mode="left_to_right"
    )
    settings: Optional[PersonSettings] = None
    phone: Optional[str] = Field(None, alias="phoneNumberKey")
    is_adult: Optional[bool] = Field(None, alias="isAdult")
    account_person_type: Optional[str] = Field(None, alias="accountPersonType")
    account_creator: Optional[bool] = Field(None, alias="accountCreator")
    notifiable: Optional[bool] = None
    version: Optional[int] = None
    notifiable_features: Optional[List[str]] = Field(None, alias="notifiableFeatures")


class CoziHousehold(BaseModel):
    """The household (account). ``photo`` is opaque: an upload response, PUT back verbatim."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    account_id: str = Field(alias="accountId")
    name: str = ""
    color_index: Optional[int] = Field(None, alias="colorIndex")
    timezone: Optional[str] = None
    cobrand: Optional[str] = None
    creation_date: Optional[str] = Field(None, alias="creationDate")
    analytics_on: Optional[bool] = Field(None, alias="analyticsOn")
    photo: Optional[Dict[str, Any]] = None


class CoziIcalFeed(BaseModel):
    """One iCal export feed. The URL is a bearer secret, so repr leaves it out."""

    model_config = ConfigDict(extra="allow")

    status: str  # "enabled" | "disabled" | "revoked" | "unset"
    url: Optional[str] = Field(None, repr=False)


class CoziSharedCalendar(BaseModel):
    """A calendar the household subscribes to. An internet calendar's URL may be private."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    url: Optional[str] = Field(None, repr=False)


class CoziCalendarSubscription(BaseModel):
    """A subscribed calendar and the members it shows for."""

    model_config = ConfigDict(extra="allow")

    calendar: CoziSharedCalendar
    attendees: List[str] = Field(default_factory=list)


def _check_color(value: Any) -> int:
    # bool is an int subclass; True would otherwise pass as colour 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoziValidationError(f"Invalid color index: {value!r}")
    return value


def _person_body(name: Any, email: Any, color_index: Any, settings: Any) -> Dict[str, Any]:
    """The web's add/edit body: always these four keys, never an id or version."""
    if not isinstance(name, str) or not name.strip():
        raise CoziValidationError("Name cannot be empty")
    if not isinstance(email, str):
        raise CoziValidationError(f"Invalid email: expected a string, got {type(email).__name__}")
    if not isinstance(settings, PersonSettings):
        raise CoziValidationError("settings must be a PersonSettings")
    if not email and settings.any_email:
        raise CoziValidationError("An email address is needed to turn on email notifications")
    return {
        "name": name,
        "email": email,
        "colorIndex": _check_color(color_index),
        "settings": settings.model_dump(by_alias=True),
    }


def _find(persons: List[CoziPerson], person_id: str) -> CoziPerson:
    for person in persons:
        if person.id == person_id:
            return person
    raise ResourceNotFoundError(f"No family member with id {person_id}")


def _ids(ids: Iterable[str]) -> List[str]:
    # A str would iterate one id per character; None or an int would raise TypeError.
    if isinstance(ids, str) or not isinstance(ids, Iterable):
        raise CoziValidationError("attendee_ids must be a list of person ids")
    return [validate_id(i, "attendee_id") for i in ids]


def _parse_secret(parse: Callable[[], T]) -> T:
    """Run a parse whose input holds bearer-secret URLs; a failure keeps only the message."""
    try:
        return parse()
    except ResponseParseError as e:
        # parse_model attaches the input and chains pydantic's error (which repeats
        # the input); either would put a feed or calendar URL into a logged traceback.
        raise ResponseParseError(str(e)) from None


def _expect(kind: type, body: Any, what: str) -> Any:
    # An empty 2xx decodes to True; never hand that back as data. No body in the message.
    if not isinstance(body, kind):
        raise ResponseParseError(f"Expected {what} as a {kind.__name__}, got {type(body).__name__}")
    return body


class AccountMixin(Transport):
    """Account endpoints. Mixed into CoziClient; uses Transport._request."""

    # ---------------------------------------------------------------- persons

    async def get_family_members(self) -> List[CoziPerson]:
        """All family members (GET /account/person/)."""
        return parse_models(CoziPerson, await self._request("GET", PERSONS))

    async def create_person(
        self,
        name: str,
        email: str = "",
        color_index: Optional[int] = None,
        settings: Optional[PersonSettings] = None,
    ) -> CoziPerson:
        """
        Add a family member (POST /account/person/) and return it as stored.

        Defaults are the web's: the first palette colour no member uses (one extra
        read of the members and the config) and ``default_settings(email)``. A
        non-empty ``email`` gives that address a login to the household and may make
        Cozi send it a verification email.
        """
        if settings is None:
            settings = default_settings(email if isinstance(email, str) else "")
        body = _person_body(name, email, 0 if color_index is None else color_index, settings)
        if color_index is None:
            body["colorIndex"] = await self._free_color()
        return parse_model(CoziPerson, await self._request("POST", PERSONS, json=body))

    async def _free_color(self) -> int:
        # The web: first index of config app.colors.attendeePalette no member uses, else 0.
        persons = await self.get_family_members()
        palette: Any = await self.get_config()
        for key in ("app", "colors", "attendeePalette"):
            palette = palette.get(key) if isinstance(palette, dict) else None
        size = len(palette) if isinstance(palette, (dict, list)) else 0
        used = {p.color for p in persons}
        return next((i for i in range(size) if i not in used), 0)

    async def update_person(self, person: CoziPerson) -> CoziPerson:
        """
        Save a member's name, email, colour and settings (PUT /account/person/{id}).

        Reads the members first: like the web, it refuses to clear an email a member
        already has (that removes their login; turn the email channels off instead).
        Setting an email gives that address a login and may send it a verification
        email. Settings flags the model does not know are sent back as read.
        Returns the member as stored.
        """
        if not isinstance(person, CoziPerson):
            raise CoziValidationError("update_person needs a CoziPerson")
        person_id = validate_id(person.id, "person_id")
        body = _person_body(person.name, person.email or "", person.color, person.settings)
        stored = _find(await self.get_family_members(), person_id)
        if stored.email and not body["email"]:
            raise CoziValidationError(
                "Cannot remove a member's email; turn off their email notifications instead"
            )
        return parse_model(CoziPerson, await self._request("PUT", PERSONS + person_id, json=body))

    async def delete_person(self, person_id: str) -> None:
        """
        Remove a family member (DELETE /account/person/{id}).

        Refused, as on the web, for the last member and for the only member with an
        email (the household's last login). Their events and to-dos stay, assigned
        to everyone.
        """
        person_id = validate_id(person_id, "person_id")
        persons = await self.get_family_members()
        target = _find(persons, person_id)
        if len(persons) == 1:
            raise CoziValidationError("Cozi needs at least one family member")
        if target.email and not any(p.email for p in persons if p.id != person_id):
            raise CoziValidationError(
                "This is the only member with an email (the household's login); "
                "add an email to another member first"
            )
        await self._request("DELETE", PERSONS + person_id)

    async def send_verification_email(self, person_id: str) -> None:
        """Have Cozi email the member a verification link. Sends real mail."""
        person_id = validate_id(person_id, "person_id")
        await self._request(
            "POST", f"{PERSONS}{person_id}/verify-email", data=b"", headers=JSON_CONTENT_TYPE
        )

    # -------------------------------------------------------------- household

    async def get_household(self) -> CoziHousehold:
        """The household: name, colour, timezone, cobrand, photo (GET /account/)."""
        return parse_model(CoziHousehold, await self._request("GET", HOUSEHOLD))

    async def update_household(
        self,
        *,
        name: Optional[str] = None,
        color_index: Optional[int] = None,
        timezone: Optional[str] = None,
    ) -> CoziHousehold:
        """
        Change the household name, colour or IANA timezone; returns it as stored.

        One PUT /account/ carrying only the given keys (the web sends one key per
        PUT). A new timezone moves when reminders fire.
        """
        body: Dict[str, Any] = {}
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise CoziValidationError("Household name cannot be empty")
            body["name"] = name
        if color_index is not None:
            body["colorIndex"] = _check_color(color_index)
        if timezone is not None:
            # ponytail: not checked against zoneinfo (absent without tzdata); Cozi decides.
            if not isinstance(timezone, str) or not timezone.strip():
                raise CoziValidationError(
                    "Timezone must be an IANA name such as 'America/New_York'"
                )
            body["timezone"] = timezone
        if not body:
            raise CoziValidationError("Nothing to update: pass name, color_index or timezone")
        return parse_model(CoziHousehold, await self._request("PUT", HOUSEHOLD, json=body))

    async def upload_household_photo(self, data: bytes, filename: str) -> CoziHousehold:
        """
        Upload a family photo (.jpg/.jpeg/.png, at most 20 MB) and make it the household's.

        As on the web: multipart field "photopicker" to /2111/{acct}/photo/uploader.json
        with the token in the query (no Bearer), then PUT /account/ {photo: <response>}.
        There is no way to put the previous photo back.
        """
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise CoziValidationError("Photo data must be non-empty bytes")
        if len(data) > MAX_PHOTO_BYTES:
            raise CoziValidationError("Photo is larger than 20 MB")
        if not isinstance(filename, str):
            raise CoziValidationError("filename must be a string")
        filename = os.path.basename(filename)  # never send the caller's directories
        content_type = PHOTO_TYPES.get(os.path.splitext(filename)[1].lower())
        if content_type is None:
            raise CoziValidationError("Photo must be a .jpg, .jpeg or .png file")
        payload = bytes(data)

        def form() -> aiohttp.FormData:
            # A FormData is single-use; the transport calls this once per attempt.
            fd = aiohttp.FormData()
            fd.add_field("photopicker", payload, filename=filename, content_type=content_type)
            return fd

        photo = await self._request(
            "POST", "/2111/{acct}/photo/uploader.json", form=form, query_token=True
        )
        photo = _expect(dict, photo, "the photo upload response")
        return parse_model(
            CoziHousehold, await self._request("PUT", HOUSEHOLD, json={"photo": photo})
        )

    # ------------------------------------------------------------------ reads

    async def get_config(self) -> Dict[str, Any]:
        """The account config (featureKeys, app.colors.attendeePalette, ...) as a plain dict."""
        return _expect(dict, await self._request("GET", "/2004/{acct}/config/"), "the config")

    async def get_personal_info(self) -> Dict[str, Any]:
        """Family name, names, external ids and devices, as a plain dict. PII: do not log it."""
        return _expect(
            dict, await self._request("GET", "/2111/{acct}/account/pii/"), "the personal info"
        )

    async def get_subscriptions(self) -> List[Dict[str, Any]]:
        """The paid-plan entitlements, as plain dicts. Holds payment details: do not log it."""
        return _expect(
            list, await self._request("GET", "/2004/{acct}/subscription/"), "the subscriptions"
        )

    # -------------------------------------------------------------- iCal feeds

    async def get_ical_feeds(self) -> Dict[str, CoziIcalFeed]:
        """iCal export feeds by person id (``FAMILY_FEED_ID`` is the whole family's)."""
        body = _expect(
            dict,
            await self._request("GET", "/2111/{acct}/icalendar/settings/"),
            "the iCal settings",
        )
        # ponytail: "scheduleFeeds" (empty on the live account) is dropped; model it once seen.
        feeds = _expect(dict, body.get("feeds"), "the iCal feeds")
        return _parse_secret(
            lambda: {key: parse_model(CoziIcalFeed, feed) for key, feed in feeds.items()}
        )

    async def set_ical_feed(self, person_id: str, enabled: bool) -> None:
        """
        Turn a member's iCal feed on, or revoke it (``FAMILY_FEED_ID`` for the family feed).

        Revoking breaks the feed URL for everyone already subscribed to it.
        """
        person_id = validate_id(person_id, "person_id")
        if not isinstance(enabled, bool):
            raise CoziValidationError(f"enabled must be True or False, got {enabled!r}")
        await self._request(
            "PUT",
            f"/2111/{{acct}}/icalendar/settings/attendee/{person_id}",
            json={"feed": {"status": "enabled" if enabled else "revoked"}},
        )

    # ------------------------------------------------------ shared calendars

    async def get_subscribed_calendars(self) -> List[CoziCalendarSubscription]:
        """Subscribed holiday and internet calendars, with the members each shows for."""
        body = await self._request("GET", SHARED_CALENDARS, unversioned=True)
        return _parse_secret(lambda: parse_models(CoziCalendarSubscription, body))

    async def subscribe_calendar(self, calendar_id: str, attendee_ids: Iterable[str] = ()) -> None:
        """
        Subscribe to a calendar (e.g. ``HOLIDAY_CALENDARS["US"]``), or change whom it shows for.

        The web subscribes holiday calendars with no attendees.
        """
        calendar_id = validate_id(calendar_id, "calendar_id")
        attendees = _ids(attendee_ids)
        await self._request(
            "PUT", f"{SHARED_CALENDARS}{calendar_id}/", json=attendees, unversioned=True
        )

    async def unsubscribe_calendar(self, calendar_id: str) -> None:
        """Remove a subscribed calendar. An internet calendar needs its URL again to come back."""
        calendar_id = validate_id(calendar_id, "calendar_id")
        await self._request("DELETE", f"{SHARED_CALENDARS}{calendar_id}/", unversioned=True)

    async def add_internet_calendar(self, url: str, attendee_ids: Iterable[str] = ()) -> str:
        """
        Make Cozi fetch an iCal URL as a calendar, subscribe to it, and return its id.

        Two calls, as on the web: POST internetcalendar/ {url}, then the subscribe PUT.
        """
        if not isinstance(url, str) or not url.strip():
            raise CoziValidationError("Calendar URL cannot be empty")
        attendees = _ids(attendee_ids)
        body = await self._request(
            "POST",
            "/calendar/sharedcalendar/internetcalendar/",
            json={"url": url.strip()},
            unversioned=True,
        )
        body = _expect(dict, body, "the internet calendar response")
        try:
            calendar_id = validate_id(body.get("calendar_source_id"), "calendar_source_id")
        except CoziValidationError:
            raise ResponseParseError(
                "Cozi registered the calendar URL but returned no usable calendar_source_id; "
                "it was not subscribed"
            ) from None
        await self.subscribe_calendar(calendar_id, attendees)
        return calendar_id
