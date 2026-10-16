"""
Calendar: appointments, birthdays, meals, search, smart add and the print view.

There is one calendar resource, the month page ``/2004/{acct}/calendar/{year}/{month}``
(``{items, days}``). Every write is a POST of a one-element array to a month page,
in the shapes the web client builds (bundle main.c43180f9, hvBi.O/S). Cozi answers
200 with the whole page even when it discards an operation, naming the reason only
in ``rejectedItems``, so every write checks it and returns what Cozi stored, read
from that page.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from ._transport import Transport, parse_model, validate_calendar_period, validate_id
from .exceptions import CoziValidationError, ResponseParseError, WriteVerificationError

logger = logging.getLogger("cozi_client")

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HMS = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_MIDNIGHT = ("00:00:00", "00:00")
# Search keys every hit, its inner id and its days refs "<uuid>-a"; the calendar
# itself uses the bare uuid (cozi_mcp, live 2026-10-13).
_SEARCH_ID = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-a$")
_WEEKDAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")
_PERIOD_MINUTES = {"Daily": 1440, "Weekly": 10080, "Monthly": 43200, "Yearly": 525600}

# The web's meal slots and the times it writes for them, verbatim (bundle module 1lJD).
MEAL_SLOTS: Dict[str, Tuple[str, str]] = {
    "breakfast": ("8:00:00", "10:00:00"),
    "lunch": ("12:00:00", "14:00:00"),
    "dinner": ("18:00:00", "20:00:00"),
    "snack": ("21:00:00", "23:00:00"),
}


def _plain_day(value: Any) -> bool:
    # A datetime is a date too, but would go out as "2019-01-15T00:00:00".
    return isinstance(value, date) and not isinstance(value, datetime)


def _iso_day(value: Any) -> Optional[date]:
    if isinstance(value, str) and _ISO_DAY.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def last_day_of(start: date, date_span: int, all_day: bool) -> date:
    """
    The last calendar day an item covers.

    ``dateSpan`` is a day offset: the item ends ``date_span`` days after ``start`` at
    its end time. An all-day item ends at the midnight after its last day, so that day
    is one earlier; a zero all-day span is read as one day.
    """
    return start + timedelta(days=max(date_span - int(all_day), 0))


def date_span_for_last_day(start: date, last_day: date, all_day: bool) -> int:
    """The ``date_span`` of an item running from ``start`` to ``last_day`` inclusive."""
    if last_day < start:
        raise CoziValidationError(f"Last day {last_day} is before the start {start}")
    return (last_day - start).days + int(all_day)


def repeat_rule(
    frequency: str,
    *,
    start_day: date,
    interval: int = 1,
    weekdays: Optional[Sequence[str]] = None,
    monthly_by: str = "date",
    until: Optional[date] = None,
) -> Dict[str, Any]:
    """
    A Cozi ``recurrence`` for a series starting on ``start_day``, in the web's rule shape.

    Args:
        frequency: "daily", "weekly", "monthly" or "yearly".
        weekdays: Weekly only: two-letter days ("MO".."SU"). Must include the start's
            weekday; defaults to it.
        monthly_by: Monthly/yearly: "date" repeats on the day of the month, "weekday"
            on its nth weekday ("2TU"; a 5th weekday becomes the last, "-1TU").
        until: Last day of the series; None repeats forever.
    """
    for day in (start_day,) if until is None else (start_day, until):
        if not _plain_day(day):
            raise CoziValidationError(f"Invalid day: {day!r}")
    freq = frequency.lower() if isinstance(frequency, str) else ""
    if freq not in ("daily", "weekly", "monthly", "yearly"):
        raise CoziValidationError(f"Unknown repeat frequency: {frequency!r}")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        raise CoziValidationError(f"Repeat interval must be a positive integer, got {interval!r}")
    if weekdays is not None and freq != "weekly":
        raise CoziValidationError("weekdays applies only to a weekly repeat")
    if monthly_by not in ("date", "weekday"):
        raise CoziValidationError(f"monthly_by must be 'date' or 'weekday', got {monthly_by!r}")
    if monthly_by == "weekday" and freq not in ("monthly", "yearly"):
        raise CoziValidationError("monthly_by applies only to a monthly or yearly repeat")

    weekday = _WEEKDAYS[(start_day.weekday() + 1) % 7]
    rule: Dict[str, Any] = {"frequency": freq.capitalize(), "interval": interval}
    if freq == "weekly":
        wanted = {str(w).upper() for w in weekdays or (weekday,)}
        if wanted - set(_WEEKDAYS):
            raise CoziValidationError(f"Unknown weekday(s): {sorted(wanted - set(_WEEKDAYS))}")
        if weekday not in wanted:
            raise CoziValidationError(
                f"A weekly repeat must include the start day's weekday ({weekday})"
            )
        rule["byDay"] = [w for w in _WEEKDAYS if w in wanted]
    elif freq != "daily":
        if freq == "yearly":
            rule["byMonth"] = [start_day.month]
        if monthly_by == "weekday":
            nth = (start_day.day + 6) // 7
            rule["byDay"] = [f"{-1 if nth == 5 else nth}{weekday}"]
        else:
            rule["byMonthDay"] = [start_day.day]
    if until is not None and until < start_day:
        raise CoziValidationError(f"Repeat until {until} is before the start {start_day}")
    rule["end"] = {"untilDay": until.isoformat()} if until is not None else {}
    return {"rules": [rule]}


class Extent(str, Enum):
    """Which occurrences of a series a scoped write touches."""

    DAY = "day"  # this occurrence only
    FORWARD = "forward"  # this one and every later one


@dataclass(frozen=True)
class Occurrence:
    """One occurrence of a series (its start day, as ``occurrence_days`` lists it) plus an extent."""

    day: date
    extent: Extent


class _WholeSeries:
    def __repr__(self) -> str:
        return "WHOLE_SERIES"


# Every occurrence, past ones included. The web client never sends this; it is the
# explicit opt-in for what an unscoped write to a series does.
WHOLE_SERIES = _WholeSeries()

Scope = Union[Occurrence, _WholeSeries, None]


class CoziAppointment(BaseModel):
    """
    One item of a calendar month page: an appointment, a birthday or a meal.

    Validates the raw wire item: ``itemDetails`` is hoisted to the top level (and not
    kept), and unknown keys ride along as extras. An all-day item has both times None
    (Cozi stores 00:00:00/00:00:00). ``date_span`` is a day offset, see ``last_day_of``.
    """

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True, extra="allow")

    id: Optional[str] = None
    subject: str = Field("", alias="description")
    start_day: date = Field(alias="day")
    start_time: Optional[time] = Field(None, alias="startTime")
    end_time: Optional[time] = Field(None, alias="endTime")
    date_span: int = Field(0, alias="dateSpan")
    attendees: List[str] = Field(default_factory=list, alias="householdMembers")
    location: Optional[str] = None
    notes: Optional[str] = None
    item_type: Optional[str] = Field(None, alias="itemType")  # appointment | birthday | meal
    item_source: Optional[str] = Field(None, alias="itemSource")  # set on subscribed calendars
    item_version: Optional[int] = Field(None, alias="itemVersion")
    reminders: Optional[List[int]] = None  # minutes before; None = not known / not set
    recurrence: Optional[Dict[str, Any]] = None
    recurrence_start_day: Optional[date] = Field(None, alias="recurrenceStartDay")
    read_only: Optional[bool] = Field(None, alias="readOnly")
    name: Optional[str] = None  # birthday
    birth_year: Optional[int] = Field(None, alias="birthYear")  # birthday
    meal_slot: Optional[str] = Field(None, alias="mealSlot")  # meal
    meal_items: Optional[List[Dict[str, Any]]] = Field(None, alias="mealItems")  # meal
    # Start day of each occurrence on the page it was read from (Cozi's `days` map).
    occurrence_days: List[date] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_wire(cls, data: Any) -> Any:
        # Only the wire shape is rewritten. Assignment re-runs this validator with
        # field names (no "day"), so nothing is re-hoisted or re-nulled then.
        if not isinstance(data, dict) or "day" not in data:
            return data
        v = dict(data)
        details = v.pop("itemDetails", None)
        if isinstance(details, dict):
            for key, value in details.items():
                # A meal's itemDetails carries its own id; the item's id wins.
                if key != "id" and v.get(key) is None:
                    v[key] = value
        if "description" in v or "descriptionShort" in v:
            v["description"] = next(
                (
                    s.strip()
                    for s in (v.get("description"), v.get("descriptionShort"))
                    if isinstance(s, str) and s.strip()
                ),
                "",
            )
        # All-day is both times at midnight; a 00:00-01:00 event keeps its start.
        if v.get("startTime") in _MIDNIGHT and v.get("endTime") in _MIDNIGHT:
            v["startTime"] = v["endTime"] = None
        return v

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _hms(cls, v: Any) -> Any:
        # Cozi writes meal times as "8:00:00", which pydantic's parser refuses.
        if isinstance(v, str) and _HMS.match(v):
            hour, minute, *second = (int(part) for part in v.split(":"))
            return time(hour, minute, second[0] if second else 0)
        return v

    @field_validator("reminders", mode="before")
    @classmethod
    def _minutes(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [r.get("minutesBefore") if isinstance(r, dict) else r for r in v]
        return v

    @property
    def is_all_day(self) -> bool:
        return self.start_time is None and self.end_time is None

    @property
    def is_recurring(self) -> bool:
        r = self.recurrence or {}
        text = r.get("text")
        return (
            bool(r.get("rules"))
            or bool(text.strip() if isinstance(text, str) else text)
            or isinstance(r.get("endDay"), str)
        )

    @property
    def skipped_days(self) -> List[date]:
        """Deleted occurrences. An exdate naming a ``standinApptId`` was edited, not deleted."""
        exdates = (self.recurrence or {}).get("exdates")
        days = set()
        for ex in exdates if isinstance(exdates, list) else []:
            if isinstance(ex, dict) and ex.get("standinApptId"):
                continue
            raw = ex.get("date") if isinstance(ex, dict) else ex
            day = _iso_day(raw[:10]) if isinstance(raw, str) else None
            if day:
                days.add(day)
        return sorted(days)

    @property
    def recurrence_text(self) -> Optional[str]:
        """Cozi's own wording of the rule ("Every Tuesday until Feb 12, 2019")."""
        text = (self.recurrence or {}).get("text")
        joined = "".join(t for t in text if isinstance(t, str)) if isinstance(text, list) else text
        return (joined.strip() or None) if isinstance(joined, str) else None

    @property
    def end_day(self) -> Optional[date]:
        """Last day of the series (``recurrence.endDay``); not the end of a multi-day item."""
        return _iso_day((self.recurrence or {}).get("endDay"))

    @property
    def last_day(self) -> date:
        return last_day_of(self.start_day, self.date_span, self.is_all_day)

    @property
    def is_editable(self) -> bool:
        """False for subscribed-calendar items, read-only items and meals (see update_meal)."""
        return self.item_source is None and not self.read_only and self.item_type != "meal"


# ------------------------------------------------------------------ wire payloads


def _new(data: Dict[str, Any]) -> CoziAppointment:
    """A model from caller arguments; bad ones are a CoziValidationError, not pydantic's."""
    try:
        return CoziAppointment.model_validate(data)
    except PydanticValidationError as e:
        raise CoziValidationError(f"Invalid calendar item: {e.errors()[0]['msg']}") from e


def _hhmm(t: Optional[time]) -> str:
    # Cozi refuses null times ("start_time and end_time are required"); all-day is 00:00/00:00.
    return t.strftime("%H:%M") if t else "00:00"


def _minutes_of(t: Optional[time]) -> int:
    return (t.hour * 60 + t.minute) if t else 0


def _details(appt: CoziAppointment) -> Dict[str, Any]:
    """The six detail keys every appointment and birthday write carries."""
    if (appt.start_time is None) != (appt.end_time is None):
        raise CoziValidationError("Set both start_time and end_time, or neither for an all-day item")
    if appt.date_span < 0:
        raise CoziValidationError(f"date_span cannot be negative, got {appt.date_span}")
    # The web never writes an all-day item shorter than a day; timed spans go out untouched.
    span = (appt.date_span or 1) if appt.is_all_day else appt.date_span
    if span == 0 and _minutes_of(appt.end_time) < _minutes_of(appt.start_time):
        raise CoziValidationError("end_time is before start_time on the same day")
    _refuse_self_overlap(appt, span)
    return {
        "startTime": _hhmm(appt.start_time),
        "endTime": _hhmm(appt.end_time),
        "dateSpan": span,
        "attendeeSet": list(appt.attendees),
        "location": appt.location,
        "notes": appt.notes,
    }


def _refuse_self_overlap(appt: CoziAppointment, span: int) -> None:
    # Port of the web form's check (chunk 1416 Me()): only for multi-day items, and
    # a length equal to the period is multiplied by the span before comparing.
    rules = (appt.recurrence or {}).get("rules")
    if not span or not isinstance(rules, list) or not rules or not isinstance(rules[0], dict):
        return
    period = _PERIOD_MINUTES.get(str(rules[0].get("frequency")))
    interval = rules[0].get("interval", 1)
    if period is None or not isinstance(interval, int):
        return
    period *= interval
    length = span * 1440 + _minutes_of(appt.end_time) - _minutes_of(appt.start_time)
    if period == length:
        length *= span
    if period < length:
        raise CoziValidationError(
            "A repeating event cannot overlap with itself; shorten it or repeat it less often"
        )


def _reminders(minutes: Sequence[int]) -> List[Dict[str, int]]:
    return [{"minutesBefore": m} for m in minutes]


def _notify_fields(notify: Optional[Sequence[str]]) -> Dict[str, Any]:
    if not notify:
        return {}
    if isinstance(notify, str):
        raise CoziValidationError("notify must be a list of person ids")
    return {"notifyPersons": [validate_id(p, "person id") for p in notify]}


def _scope_fields(occ: Optional[Occurrence]) -> Dict[str, Any]:
    return {"selectedDay": occ.day.isoformat(), "extent": Extent(occ.extent).value} if occ else {}


def _appointment_op(
    appt: CoziAppointment, op: str, occ: Optional[Occurrence], notify: Optional[Sequence[str]]
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"id": appt.id} if op == "edit" else {}
    body["startDay"] = appt.start_day.isoformat()
    if appt.recurrence is not None:
        # A sibling of details, re-sent on every edit: an edit replaces the item whole.
        body["recurrence"] = appt.recurrence
    if appt.reminders is not None:
        body["reminders"] = _reminders(appt.reminders)  # [] clears them on an edit
    body["details"] = {**_details(appt), "subject": appt.subject}
    # Never itemVersion: Cozi then discards the edit (live 2026-07-24).
    return {"itemType": "appointment", **_notify_fields(notify), **_scope_fields(occ), op: body}


def _birthday_op(appt: CoziAppointment, op: str) -> Dict[str, Any]:
    day = appt.start_day
    # The web rebuilds the yearly rule from the day on every save; Feb 29 is the month's last day.
    rule = {
        "byMonth": [day.month],
        "byMonthDay": [-1] if (day.month, day.day) == (2, 29) else [day.day],
        "interval": 1,
        "frequency": "Yearly",
    }
    body: Dict[str, Any] = {"id": appt.id} if op == "edit" else {}
    body["startDay"] = day.isoformat()
    if appt.reminders is not None:
        body["reminders"] = _reminders(appt.reminders)
    body["recurrence"] = {"rules": [rule]}
    body["itemType"] = "birthday"
    body["details"] = {**_details(appt), "name": appt.name, "birthYear": appt.birth_year or None}
    return {"itemType": "birthday", op: body}


def _meal_op(meal: CoziAppointment, op: str) -> Dict[str, Any]:
    slot = (meal.meal_slot or "").lower()
    if slot not in MEAL_SLOTS:
        raise CoziValidationError(
            f"Meal slot must be one of {sorted(MEAL_SLOTS)}, got {meal.meal_slot!r}"
        )
    items = [
        {k: item[k] for k in ("text", "recipeBoxId") if item.get(k)}
        for item in meal.meal_items or []
        if isinstance(item, dict)
    ]
    if not items or not all(isinstance(i.get("text"), str) and i["text"].strip() for i in items):
        raise CoziValidationError("A meal needs at least one dish, each with text")
    start, end = MEAL_SLOTS[slot]
    body: Dict[str, Any] = {"id": meal.id} if op == "edit" else {}
    body["startDay"] = meal.start_day.isoformat()
    body["itemType"] = "meal"
    body["details"] = {
        "subject": items[0]["text"],
        "startTime": start,
        "endTime": end,
        "mealItems": items,
        "mealSlot": slot,
        "notes": meal.notes,
    }
    return {"itemType": "meal", op: body}


def _delete_op(
    appt: CoziAppointment, scope: Dict[str, Any], notify: Optional[Sequence[str]]
) -> Dict[str, Any]:
    target: Dict[str, Any] = {"id": appt.id}
    if appt.item_version is not None:
        # Cozi enforces it: a stale version is rejected ("version mismatch", live 2026-10-16).
        target["version"] = appt.item_version
    # The web hard-codes "appointment" here for birthdays and meals too.
    return {"itemType": "appointment", "delete": target, **scope, **_notify_fields(notify)}


def _assert_not_rejected(body: Any, operation: str, appt_id: Optional[str] = None) -> None:
    """Raise WriteVerificationError if Cozi discarded the operation (a 200 with ``rejectedItems``)."""
    rejected = body.get("rejectedItems") if isinstance(body, dict) else None
    if not isinstance(rejected, list) or not rejected:
        return
    # A rejection naming no id cannot be attributed, so it counts as ours.
    mine = [
        r for r in rejected if not appt_id or not isinstance(r, dict) or r.get("id") in (None, appt_id)
    ]
    if not mine:
        return
    reasons = "; ".join(
        r["error"] if isinstance(r, dict) and isinstance(r.get("error"), str) else "no reason given"
        for r in mine
    )
    raise WriteVerificationError(
        f"Cozi rejected the {operation}{f' of {appt_id}' if appt_id else ''}: {reasons}",
        response_data={"rejectedItems": mine},
    )


def _page_path(day: date) -> str:
    year, month = validate_calendar_period(day.year, day.month)
    return f"/2004/{{acct}}/calendar/{year}/{month}"


def _items_of(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(body.get("items"), dict):
        raise ResponseParseError(
            "Expected a calendar page {items, days} from Cozi",
            response_data=body if isinstance(body, dict) else {"response": body},
        )
    return body["items"]


def _occurrence_index(days: Any) -> Dict[str, List[date]]:
    """
    Start day of each occurrence per item key, from a page's ``days`` map.

    Cozi expands series, drops deleted occurrences and lists a multi-day item on
    every day it covers, naming its occurrence as ``multiday.startDay`` (which may lie
    in the previous month). Port of cozi_mcp occurrenceIndex.
    """
    starts: Dict[str, set] = {}
    for key, refs in days.items() if isinstance(days, dict) else []:
        day = _iso_day(key)
        if day is None or not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get("id"), str):
                continue
            multiday = ref.get("multiday")
            start = _iso_day(multiday.get("startDay")) if isinstance(multiday, dict) else None
            starts.setdefault(ref["id"], set()).add(start or day)
    return {k: sorted(v) for k, v in starts.items()}


def _parse_item(item_id: str, item: Any, days: List[date]) -> Optional[CoziAppointment]:
    if not isinstance(item, dict):
        logger.warning("Skipping calendar item %s: not an object", item_id)
        return None
    try:
        return parse_model(CoziAppointment, {**item, "id": item_id, "occurrence_days": days})
    except ResponseParseError as e:
        logger.warning("Skipping calendar item %s: %s", item_id, e)
        return None


def _parse_page(body: Any) -> Dict[str, CoziAppointment]:
    """The items of a month page by id; an item that does not parse is skipped with a warning."""
    items = _items_of(body)
    index = _occurrence_index(body.get("days"))
    page = {}
    for key, item in items.items():
        item_id = (item.get("id") or key) if isinstance(item, dict) else key
        parsed = _parse_item(item_id, item, index.get(key, []))
        if parsed is not None:
            page[key] = parsed
    return page


def _same_time(a: Optional[time], b: Optional[time]) -> bool:
    return _hhmm(a) == _hhmm(b)


# ------------------------------------------------------------------ the mixin


class CalendarMixin(Transport):
    """Calendar endpoints. Mixed into CoziClient; uses Transport._request."""

    # --------------------------------------------------------------- reads

    async def get_calendar(self, year: int, month: int) -> List[CoziAppointment]:
        """
        Every item on a month page (1-12), each with its ``occurrence_days`` that month.

        A series or multi-day item is one entry (keyed by its id on every page it
        touches); ``occurrence_days`` says where it falls. Merge pages by id.
        """
        year, month = validate_calendar_period(year, month)
        return list((await self._get_page_path(f"/2004/{{acct}}/calendar/{year}/{month}")).values())

    async def search_calendar(self, query: str) -> Tuple[List[CoziAppointment], bool]:
        """
        Cozi's calendar search (a paid-plan feature: 402/403 is PermissionDeniedError).

        Returns ``(items, has_more)``; there is no way to fetch further pages. The index
        trails writes by a few seconds and does not reach back more than a few years.
        """
        if not isinstance(query, str) or not query.strip():
            raise CoziValidationError("Search query cannot be empty")
        body = await self._request("GET", "/2004/{acct}/search/calendar/", params={"q": query.strip()})
        index = _occurrence_index(body.get("days") if isinstance(body, dict) else None)
        found = []
        for key, hit in _items_of(body).items():
            item = hit.get("item", hit) if isinstance(hit, dict) else hit
            match = _SEARCH_ID.match(key)
            parsed = _parse_item(match.group(1) if match else key, item, index.get(key, []))
            if parsed is not None:
                found.append(parsed)
        return found, body.get("hasNext") is True

    async def get_calendar_print(self, view: str, day: date) -> bytes:
        """The printable ``view`` ("week" or "month") containing ``day``: a PDF, as Cozi renders it."""
        if view not in ("week", "month"):
            raise CoziValidationError(f"Print view must be 'week' or 'month', got {view!r}")
        if not isinstance(day, date):
            raise CoziValidationError(f"Invalid day: {day!r}")
        return await self._request(
            "GET", f"/2207/{{acct}}/printing/calendar/{view}/{day:%Y%m%d}", raw=True
        )

    async def parse_event(self, text: str, now: Optional[datetime] = None) -> CoziAppointment:
        """
        Cozi's smart add: turn free text ("dentist tuesday 3pm") into an unsaved item.

        Nothing is stored; pass the result to create_appointment. ``now`` anchors
        relative dates (default: local time with its offset, as the web sends it).
        """
        if not isinstance(text, str) or not text.strip():
            raise CoziValidationError("Text to parse cannot be empty")
        now = now or datetime.now()
        if now.tzinfo is None:
            now = now.astimezone()
        body = await self._request(
            "POST",
            "/2307/{acct}/calendar/parse/",
            json={"text": text, "now": now.isoformat(timespec="seconds")},
        )
        event = body.get("parsedEvent") if isinstance(body, dict) else None
        if not isinstance(event, dict):
            raise ResponseParseError(
                "Expected a parsedEvent from Cozi's smart add", response_data={"response": body}
            )
        # The web fills what the parser left open the same way (chunk 1416 @23649). The
        # parser also mints an id that names nothing stored (live 2026-10-16); drop it.
        return parse_model(
            CoziAppointment,
            {
                **event,
                "id": None,
                "day": event.get("day") or now.date().isoformat(),
                "startTime": event.get("startTime") or "00:00:00",
                "endTime": event.get("endTime") or "00:00:00",
            },
        )

    # -------------------------------------------------------------- writes

    async def create_appointment(
        self, appt: CoziAppointment, notify: Optional[Sequence[str]] = None
    ) -> CoziAppointment:
        """
        Create ``appt`` (its id is ignored) and return the item Cozi stored.

        Set ``recurrence`` (see repeat_rule) for a series and ``reminders`` in minutes.
        ``notify`` lists person ids Cozi should tell about the change.
        """
        if appt.item_type not in (None, "appointment"):
            # _appointment_op would turn it into a plain appointment, dropping what makes it one.
            raise CoziValidationError(
                f"A {appt.item_type!r} item is created with create_birthday or create_meal, "
                "not create_appointment"
            )
        if not appt.subject.strip():
            raise CoziValidationError("Appointment subject cannot be empty")
        op = _appointment_op(appt, "create", None, notify)
        return await self._create(
            appt.start_day,
            op,
            lambda a: a.subject == appt.subject.strip() and a.start_day == appt.start_day,
        )

    async def update_appointment(
        self, appt: CoziAppointment, scope: Scope = None, notify: Optional[Sequence[str]] = None
    ) -> CoziAppointment:
        """
        Replace an appointment or birthday with ``appt`` and return what Cozi stored.

        The edit replaces the item whole, so start from one read with get_calendar.
        Reminders are re-sent unless None (``[]`` clears them).

        A series needs a ``scope``:
        - ``Occurrence(day, Extent.DAY)``: that occurrence only. Set ``appt.start_day``
          to where it should now start (normally ``day``). Returns the stand-in Cozi
          stores for it, which has a new id.
        - ``Occurrence(day, Extent.FORWARD)``: that occurrence and every later one.
          Returns the new series (or the same one, from its first occurrence).
        - ``WHOLE_SERIES``: every occurrence, past ones included.

        ``day`` must be one of ``appt.occurrence_days``: read the item from the
        occurrence's own month. Birthdays take no scope.
        """
        validate_id(appt.id, "appointment id")
        occ = self._writable(appt, scope, notify)
        if appt.item_type == "birthday":
            op = _birthday_op(appt, "edit")
        else:
            # start_day still on the series start, or on another occurrence: the caller forgot
            # to move it to the occurrence, and Cozi would put the changed copy there.
            if (
                occ
                and appt.start_day != occ.day
                and (
                    appt.start_day in appt.occurrence_days or appt.start_day == appt.recurrence_start_day
                )
            ):
                raise CoziValidationError(
                    f"start_day {appt.start_day} is another occurrence of {appt.id}; set start_day to "
                    f"where the {occ.day} occurrence should start"
                )
            op = _appointment_op(appt, "edit", occ, notify)
        page = await self._write(appt.start_day, op, "edit", appt.id)
        # Not start_day as a fallback for an unknown series start: the caller moved it to occ.day.
        if occ and not (occ.extent == Extent.FORWARD and occ.day == appt.recurrence_start_day):
            return await self._stored_occurrence_edit(appt, occ, page)
        # A forward edit from the first occurrence edits the series in place (live 2026-10-16).
        if appt.id not in page and appt.is_recurring and appt.occurrence_days:
            # A series with no day left in its start month is not on that page (live
            # 2026-10-16); look on the page it was read from.
            read_from = appt.occurrence_days[0]
            if (read_from.year, read_from.month) != (appt.start_day.year, appt.start_day.month):
                page = await self._get_page_path(_page_path(read_from))
        if appt.id not in page:
            raise WriteVerificationError(
                f"Cozi accepted the edit of {appt.id}, but it is not on the page"
            )
        return page[appt.id]

    async def delete_appointment(
        self, appt: CoziAppointment, scope: Scope = None, notify: Optional[Sequence[str]] = None
    ) -> bool:
        """
        Delete an appointment or birthday. A series needs a ``scope`` (see update_appointment).

        ``delete.version`` is sent when ``item_version`` is known, and Cozi refuses a
        stale one (WriteVerificationError "version mismatch"): re-read after any change
        to the item, including a scoped write to one of its occurrences. Meals: see
        delete_meal.
        """
        validate_id(appt.id, "appointment id")
        occ = self._writable(appt, scope, notify)
        if appt.item_type == "birthday":
            # The web ends a birthday from the selected year on; from its first day that is all of it.
            fields: Dict[str, Any] = {
                "selectedDay": appt.start_day.isoformat(),
                "extent": Extent.FORWARD.value,
            }
        else:
            fields = _scope_fields(occ)
        await self._write(
            occ.day if occ else appt.start_day, _delete_op(appt, fields, notify), "delete", appt.id
        )
        return True

    async def create_birthday(
        self,
        name: str,
        day: date,
        birth_year: Optional[int] = None,
        reminders: Optional[Sequence[int]] = (10080, 1440),
    ) -> CoziAppointment:
        """
        Add a yearly birthday for ``name`` on ``day``'s month and day (reminders default:
        a week and a day before; None sends none). Cozi starts the series in
        ``birth_year``, or in 1904 when it is None (live 2026-10-16), so that is the
        ``start_day`` it reads back with.
        """
        if not isinstance(name, str) or not name.strip():
            raise CoziValidationError("Birthday name cannot be empty")
        appt = _new(
            {
                "start_day": day,
                "item_type": "birthday",
                "name": name.strip(),
                "birth_year": birth_year,
                "reminders": reminders,
            }
        )
        if appt.birth_year and appt.birth_year > appt.start_day.year:
            # Cozi would start the series after `day`, off the page the create is read back from.
            raise CoziValidationError(f"birth_year {appt.birth_year} is after {appt.start_day}")
        return await self._create(
            appt.start_day,
            _birthday_op(appt, "create"),
            lambda a: a.item_type == "birthday" and a.name == name.strip(),
        )

    async def create_meal(
        self,
        day: date,
        slot: str,
        items: Sequence[Union[str, Tuple[str, Optional[str]]]],
        notes: str = "",  # the web's default
    ) -> CoziAppointment:
        """
        Plan a meal: ``slot`` is breakfast, lunch, dinner or snack; ``items`` are dish
        texts, or ``(text, recipe_box_id)`` for a dish from the recipe box.
        """
        if isinstance(items, str):
            raise CoziValidationError("items must be a list of dishes, not a single string")
        try:
            dishes = [
                {"text": i} if isinstance(i, str) else {"text": i[0], "recipeBoxId": i[1]}
                for i in items
            ]
        except (TypeError, IndexError, KeyError):
            raise CoziValidationError("Each dish is a text or a (text, recipe_box_id) pair") from None
        meal = _new(
            {
                "start_day": day,
                "item_type": "meal",
                "meal_slot": slot,
                "meal_items": dishes,
                "notes": notes,
            }
        )
        return await self._create(
            meal.start_day,
            _meal_op(meal, "create"),
            lambda a: a.item_type == "meal" and a.start_day == meal.start_day,
        )

    async def update_meal(self, meal: CoziAppointment) -> CoziAppointment:
        """Replace a meal read from get_calendar with ``meal`` and return what Cozi stored."""
        validate_id(meal.id, "meal id")
        if meal.item_type != "meal":
            raise CoziValidationError(f"{meal.id} is not a meal")
        page = await self._write(meal.start_day, _meal_op(meal, "edit"), "edit", meal.id)
        if meal.id not in page:
            raise WriteVerificationError(
                f"Cozi accepted the edit of {meal.id}, but it is not on the page"
            )
        return page[meal.id]

    async def delete_meal(self, meal: CoziAppointment) -> bool:
        """Delete a meal read from get_calendar."""
        validate_id(meal.id, "meal id")
        if meal.item_type != "meal":
            raise CoziValidationError(f"{meal.id} is not a meal")
        fields = {"selectedDay": meal.start_day.isoformat(), "extent": Extent.DAY.value}
        await self._write(meal.start_day, _delete_op(meal, fields, None), "delete", meal.id)
        return True

    # ------------------------------------------------------------- helpers

    async def _get_page_path(self, path: str) -> Dict[str, CoziAppointment]:
        return _parse_page(await self._request("GET", path))

    async def _write(
        self, day: date, op: Dict[str, Any], operation: str, appt_id: Optional[str] = None
    ) -> Dict[str, CoziAppointment]:
        """POST one operation to ``day``'s month page; returns the page Cozi answers with."""
        body = await self._request("POST", _page_path(day), json=[op])
        _assert_not_rejected(body, operation, appt_id)
        return _parse_page(body)

    async def _create(
        self, day: date, op: Dict[str, Any], match: Callable[[CoziAppointment], bool]
    ) -> CoziAppointment:
        # Cozi never names a new id: it is the one id the answering page has that the
        # page did not have before.
        # ponytail: a concurrent create on the same page can race this diff; `match`
        # narrows it, a per-page lock would close it if that ever matters.
        before = set(_items_of(await self._request("GET", _page_path(day))))
        page = await self._write(day, op, "create")
        new = [a for key, a in page.items() if key not in before]
        if len(new) > 1:
            new = [a for a in new if match(a)]
        if len(new) != 1:
            found = len(new) or "no"
            raise WriteVerificationError(
                f"Cozi accepted the create, but {found} new matching items are on the page"
            )
        return new[0]

    def _writable(
        self, appt: CoziAppointment, scope: Scope, notify: Optional[Sequence[str]]
    ) -> Optional[Occurrence]:
        """Refuse, before any I/O, a write the web client would never make; return its occurrence."""
        if appt.item_source is not None or appt.read_only:
            raise CoziValidationError(
                f"{appt.id} comes from a subscribed or read-only calendar and cannot be changed"
            )
        if appt.item_type == "meal":
            raise CoziValidationError(f"{appt.id} is a meal; use update_meal or delete_meal")
        if appt.item_type not in (None, "appointment", "birthday"):
            raise CoziValidationError(
                f"{appt.id} is a {appt.item_type!r} item, which this client cannot change"
            )
        if scope is not None and scope is not WHOLE_SERIES and not isinstance(scope, Occurrence):
            raise CoziValidationError(
                f"scope must be an Occurrence, WHOLE_SERIES or None, got {scope!r}"
            )
        if appt.item_type == "birthday":
            if isinstance(scope, Occurrence) or notify:
                raise CoziValidationError("A birthday is changed as a whole, without a scope or notify")
            return None
        if not isinstance(scope, Occurrence):
            if scope is None and appt.is_recurring:
                raise CoziValidationError(
                    f"{appt.id} is a recurring series: pass scope=Occurrence(day, Extent.DAY or "
                    "Extent.FORWARD) for some occurrences, or WHOLE_SERIES for every one"
                )
            return None
        if not _plain_day(scope.day) or not isinstance(scope.extent, Extent):
            raise CoziValidationError(f"Invalid occurrence: {scope!r}")
        if not appt.is_recurring:
            raise CoziValidationError(f"{appt.id} is not recurring; it takes no occurrence scope")
        first, last = appt.recurrence_start_day, appt.end_day
        if (first and scope.day < first) or (last and scope.day > last):
            raise CoziValidationError(
                f"{scope.day} is outside the series {appt.id} ({first} to {last or 'open-ended'})"
            )
        # A day the page does not list can hit nothing, or worse (cozi_mcp saw a
        # selectedDay past the series end make the whole series vanish).
        if appt.occurrence_days and scope.day not in appt.occurrence_days:
            raise CoziValidationError(
                f"{scope.day} is not an occurrence of {appt.id} on the page it was read from; "
                "read it from that occurrence's month"
            )
        return scope

    async def _stored_occurrence_edit(
        self, appt: CoziAppointment, occ: Occurrence, home: Dict[str, CoziAppointment]
    ) -> CoziAppointment:
        """
        The item Cozi stored for an occurrence edit.

        DAY: a stand-in with a new id, linked from the series' exdate for that day as
        ``standinApptId``. FORWARD: a new series starting on ``appt.start_day``, or
        ``appt`` itself edited in place when that was its first occurrence (reached here
        only when ``recurrence_start_day`` is unknown). Both live on ``home``, the page
        of ``appt.start_day``. A series with no day left on
        that page is not listed there; its exdate is then read from the occurrence's
        own page, and failing that the stand-in is matched by what was sent. Port of
        cozi_mcp storedOccurrenceEdit.
        """

        # No id check: after a split the old series keeps its own start day, and the DAY
        # branch excludes it as recurring; FORWARD must still find an in-place edit.
        def looks_like(a: CoziAppointment) -> bool:
            return (
                a.start_day == appt.start_day
                and a.subject == appt.subject.strip()
                and _same_time(a.start_time, appt.start_time)
                and _same_time(a.end_time, appt.end_time)
            )

        if occ.extent == Extent.DAY:
            series = home.get(appt.id)  # type: ignore[arg-type]
            if series is None and (occ.day.year, occ.day.month) != (
                appt.start_day.year,
                appt.start_day.month,
            ):
                other = await self._get_page_path(_page_path(occ.day))
                series = other.get(appt.id)  # type: ignore[arg-type]
            exdates = (series.recurrence or {}).get("exdates") if series else None
            standin = next(
                (
                    e["standinApptId"]
                    for e in (exdates if isinstance(exdates, list) else [])
                    if isinstance(e, dict)
                    and str(e.get("date", "")).startswith(occ.day.isoformat())
                    and isinstance(e.get("standinApptId"), str)
                ),
                None,
            )
            if standin:
                candidates = [home[standin]] if standin in home else []
            elif series:
                candidates = []
            else:
                candidates = [a for a in home.values() if looks_like(a) and not a.is_recurring]
        else:
            candidates = [a for a in home.values() if looks_like(a) and a.is_recurring]
        if len(candidates) != 1:
            raise WriteVerificationError(
                f"Cozi accepted the edit of {appt.id} on {occ.day}, but the changed copy "
                + (
                    f"cannot be told apart from {len(candidates) - 1} other(s)"
                    if candidates
                    else "could not be found"
                )
            )
        return candidates[0]
