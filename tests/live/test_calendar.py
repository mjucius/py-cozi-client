"""
Live: calendar reads and writes against the real Cozi API, in the 2019 sandbox
(and 2022-01 for search). Every write is checked against a raw read of the page.
"""

import asyncio
import hashlib
import json
import random
import string
from datetime import date, datetime, time, timezone

import pytest
import pytest_asyncio

from cozi_client.calendar import (
    WHOLE_SERIES,
    CoziAppointment,
    Extent,
    Occurrence,
    date_span_for_last_day,
    repeat_rule,
)
from cozi_client.exceptions import (
    CoziException,
    CoziValidationError,
    PermissionDeniedError,
    WriteVerificationError,
)
from tests.live.conftest import PREFIX, SANDBOX, SEARCH_MONTH, domain_sweeper, subject

_sweep = domain_sweeper("calendar")


def d(month: int, day: int, year: int = 2019) -> date:
    return date(year, month, day)


def page_path(year: int, month: int) -> str:
    return f"/2004/{{acct}}/calendar/{year}/{month}"


async def raw_page(cozi, year=2019, month=1):
    return await cozi._request("GET", page_path(year, month))


async def raw_item(cozi, item_id, year=2019, month=1):
    return (await raw_page(cozi, year, month))["items"].get(item_id)


def digest(page) -> str:
    # Compare whole pages by digest, so a failure never prints the family's own items.
    return hashlib.sha256(json.dumps(page, sort_keys=True).encode()).hexdigest()


async def listed(cozi, item_id, year=2019, month=1) -> bool:
    # A bool, so a failed assert never prints the page (the family's own items).
    return item_id in (await raw_page(cozi, year, month))["items"]


async def raw_days(cozi, item_id, year=2019, month=1):
    days = (await raw_page(cozi, year, month))["days"]
    return sorted(day for day, refs in days.items() if any(r["id"] == item_id for r in refs))


async def months_listing(cozi, item_id):
    """Which sandbox months list the item: Cozi's own reading of its span."""
    return [m for y, m in SANDBOX if item_id in (await raw_page(cozi, y, m))["items"]]


async def fresh(cozi, item_id, year=2019, month=1):
    return next(a for a in await cozi.get_calendar(year, month) if a.id == item_id)


async def raw_delete(cozi, item_id, day):
    await cozi._request(
        "POST",
        page_path(day.year, day.month),
        json=[{"itemType": "appointment", "delete": {"id": item_id}}],
    )


def expect_raw(raw, day, start, end, span):
    assert (raw["day"], raw["startTime"], raw["endTime"], raw.get("dateSpan", 0)) == (
        day.isoformat(),
        start,
        end,
        span,
    )


@pytest_asyncio.fixture
async def cozi(live_client):
    live_client.made = []  # (id, day) to delete afterwards with a raw bare delete
    yield live_client
    for item_id, day in live_client.made:
        try:
            await raw_delete(live_client, item_id, day)
        except CoziException:
            pass


def track(cozi, appt: CoziAppointment) -> CoziAppointment:
    cozi.made.append((appt.id, appt.start_day))
    return appt


async def create(cozi, start_day, start=None, end=None, span=0, **kw):
    appt = CoziAppointment(
        start_day=start_day,
        subject=subject("calendar"),
        start_time=start,
        end_time=end,
        date_span=span,
        **kw,
    )
    return track(cozi, await cozi.create_appointment(appt))


async def a_member(cozi) -> str:
    persons = await cozi._request("GET", "/2004/{acct}/account/person/")
    return next(p["accountPersonId"] for p in persons if PREFIX not in (p.get("name") or ""))


# ------------------------------------------------------------------ spans


@pytest.mark.parametrize(
    "start_day, start, end, last, raw_times, span, months",
    [
        (d(1, 31), time(9), time(17), d(1, 31), ("09:00:00", "17:00:00"), 0, [1]),  # timed same day
        (d(1, 31), time(20), time(2), d(2, 1), ("20:00:00", "02:00:00"), 1, [1, 2]),  # overnight
        (d(1, 30), time(10), time(11), d(2, 1), ("10:00:00", "11:00:00"), 2, [1, 2]),  # three days
        (d(1, 31), None, None, d(1, 31), ("00:00:00", "00:00:00"), 1, [1]),  # all-day single
        (d(1, 30), None, None, d(2, 1), ("00:00:00", "00:00:00"), 3, [1, 2]),  # all-day cross-month
        (d(1, 15), time(0), time(1), d(1, 15), ("00:00:00", "01:00:00"), 0, [1]),  # starts at midnight
    ],
)
async def test_create_spans(cozi, start_day, start, end, last, raw_times, span, months):
    all_day = start is None
    stored = await create(cozi, start_day, start, end, date_span_for_last_day(start_day, last, all_day))
    expect_raw(await raw_item(cozi, stored.id), start_day, *raw_times, span)
    assert await months_listing(cozi, stored.id) == months
    assert (
        stored.start_time,
        stored.end_time,
        stored.is_all_day,
        stored.date_span,
        stored.last_day,
    ) == (start, end, all_day, span, last)
    assert stored == await fresh(cozi, stored.id)


async def test_switch_timed_to_all_day_and_back(cozi):
    stored = await create(cozi, d(1, 30), time(10), time(11), 2)
    stored.start_time = stored.end_time = None
    stored.date_span = date_span_for_last_day(d(1, 30), d(2, 1), True)
    all_day = await cozi.update_appointment(stored)
    expect_raw(await raw_item(cozi, stored.id), d(1, 30), "00:00:00", "00:00:00", 3)
    assert await months_listing(cozi, stored.id) == [1, 2]
    assert (all_day.is_all_day, all_day.last_day) == (True, d(2, 1))
    assert all_day == await fresh(cozi, stored.id)

    all_day.start_time, all_day.end_time = time(10), time(11)
    all_day.date_span = date_span_for_last_day(d(1, 30), d(2, 1), False)
    timed = await cozi.update_appointment(all_day)
    expect_raw(await raw_item(cozi, stored.id), d(1, 30), "10:00:00", "11:00:00", 2)
    assert (timed.is_all_day, timed.last_day) == (False, d(2, 1))
    assert timed == await fresh(cozi, stored.id)


async def test_midnight_start_survives_an_edit(cozi):
    stored = await create(cozi, d(1, 15), time(0), time(1))
    stored.notes = "edited"
    edited = await cozi.update_appointment(stored)
    raw = await raw_item(cozi, stored.id)
    expect_raw(raw, d(1, 15), "00:00:00", "01:00:00", 0)
    assert raw["itemDetails"]["notes"] == "edited"
    assert (edited.start_time, edited.notes) == (time(0), "edited")


async def test_attendees_location_notes(cozi):
    member = await a_member(cozi)
    stored = await create(
        cozi, d(1, 15), time(9), time(10), attendees=[member], location="Live Loc", notes="live notes"
    )
    raw = await raw_item(cozi, stored.id)
    assert (raw["householdMembers"], raw["itemDetails"]["location"], raw["itemDetails"]["notes"]) == (
        [member],
        "Live Loc",
        "live notes",
    )
    assert (stored.attendees, stored.location, stored.notes) == ([member], "Live Loc", "live notes")


async def test_reminders_set_kept_and_cleared(cozi):
    stored = await create(cozi, d(1, 16), time(10), time(11), reminders=[1440, 30])
    assert (await raw_item(cozi, stored.id))["reminders"] == [
        {"minutesBefore": 1440},
        {"minutesBefore": 30},
    ]
    assert stored.reminders == [1440, 30]

    stored.reminders = None  # not sent: Cozi keeps them
    stored.notes = "edited"
    kept = await cozi.update_appointment(stored)
    raw = await raw_item(cozi, stored.id)
    assert (raw["reminders"], raw["itemDetails"]["notes"]) == (
        [{"minutesBefore": 1440}, {"minutesBefore": 30}],
        "edited",
    )
    assert kept.reminders == [1440, 30]

    kept.reminders = []
    cleared = await cozi.update_appointment(kept)
    assert "reminders" not in await raw_item(cozi, stored.id)
    assert cleared.reminders is None


async def test_delete_version_fresh_and_stale(cozi):
    stored = await create(cozi, d(3, 5), time(10), time(11))
    stale = stored.model_copy()
    stored.notes = "bump the version"
    edited = await cozi.update_appointment(stored)
    assert edited.item_version != stale.item_version
    with pytest.raises(WriteVerificationError, match="version mismatch"):
        await cozi.delete_appointment(stale)
    assert await months_listing(cozi, stored.id) == [3]
    assert await cozi.delete_appointment(edited) is True
    assert await months_listing(cozi, stored.id) == []


# ------------------------------------------------------------------ series


def weekly(start_day, until):
    return repeat_rule("weekly", start_day=start_day, until=until)


async def create_series(cozi, start_day=d(1, 8), until=d(2, 12), start=time(7), end=time(7, 30)):
    return await create(cozi, start_day, start, end, recurrence=weekly(start_day, until))


async def test_series_shape_and_whole_series_edit(cozi):
    s = await create_series(cozi)
    raw = await raw_item(cozi, s.id)
    assert raw["itemDetails"]["recurrence"] == {
        "endDay": "2019-02-12",
        "rules": [
            {"frequency": "Weekly", "interval": 1, "end": {"untilDay": "2019-02-12"}, "byDay": ["TU"]}
        ],
        "text": ["Every Tuesday", " until Feb 12, 2019"],
    }
    assert (s.is_recurring, s.end_day, s.recurrence_text) == (
        True,
        d(2, 12),
        "Every Tuesday until Feb 12, 2019",
    )
    assert s.occurrence_days == [d(1, 8), d(1, 15), d(1, 22), d(1, 29)]

    # A later page carries the series under its original start day (live 2026-10-16).
    feb = await fresh(cozi, s.id, 2019, 2)
    assert (feb.start_day, feb.recurrence_start_day, feb.occurrence_days) == (
        d(1, 8),
        d(1, 8),
        [d(2, 5), d(2, 12)],
    )

    feb.notes = "no scope"
    with pytest.raises(CoziValidationError, match="recurring series"):
        await cozi.update_appointment(feb)
    assert "notes" not in (await raw_item(cozi, s.id))["itemDetails"]

    feb.notes = "all of them"
    stored = await cozi.update_appointment(feb, scope=WHOLE_SERIES)
    assert (await raw_item(cozi, s.id))["itemDetails"]["notes"] == "all of them"
    assert await raw_days(cozi, s.id) == ["2019-01-08", "2019-01-15", "2019-01-22", "2019-01-29"]
    assert await raw_days(cozi, s.id, 2019, 2) == ["2019-02-05", "2019-02-12"]
    assert stored == await fresh(cozi, s.id)

    # Forward from the first occurrence edits the series in place.
    stored.start_time, stored.end_time = time(8), time(8, 30)
    moved = await cozi.update_appointment(stored, scope=Occurrence(d(1, 8), Extent.FORWARD))
    assert moved.id == s.id
    raw = await raw_item(cozi, s.id)
    expect_raw(raw, d(1, 8), "08:00:00", "08:30:00", 0)
    assert await raw_days(cozi, s.id) == ["2019-01-08", "2019-01-15", "2019-01-22", "2019-01-29"]


async def test_occurrence_edits(cozi):
    s = await create_series(cozi)
    # One occurrence, moved a day later: a stand-in with a new id.
    s.start_day = d(1, 16)
    s.notes = "own copy"
    standin = track(cozi, await cozi.update_appointment(s, scope=Occurrence(d(1, 15), Extent.DAY)))
    assert standin.id != s.id
    assert (standin.start_day, standin.is_recurring, standin.notes) == (d(1, 16), False, "own copy")
    expect_raw(await raw_item(cozi, standin.id), d(1, 16), "07:00:00", "07:30:00", 0)
    assert await raw_days(cozi, s.id) == ["2019-01-08", "2019-01-22", "2019-01-29"]
    series = await fresh(cozi, s.id)
    assert series.skipped_days == []  # edited, not deleted
    assert standin == await fresh(cozi, standin.id)

    # This one and the rest: a new series from Jan 22; the old one ends before it.
    series.start_day = d(1, 22)
    series.start_time, series.end_time = time(9), time(9, 30)
    nxt = track(cozi, await cozi.update_appointment(series, scope=Occurrence(d(1, 22), Extent.FORWARD)))
    assert nxt.id not in (s.id, standin.id)
    assert (nxt.start_day, nxt.is_recurring, nxt.end_day) == (d(1, 22), True, d(2, 12))
    assert await raw_days(cozi, s.id) == ["2019-01-08"]
    assert await raw_days(cozi, nxt.id) == ["2019-01-22", "2019-01-29"]
    assert await raw_days(cozi, nxt.id, 2019, 2) == ["2019-02-05", "2019-02-12"]
    expect_raw(await raw_item(cozi, nxt.id), d(1, 22), "09:00:00", "09:30:00", 0)


async def test_occurrence_deletes(cozi):
    s = await create_series(cozi)
    await cozi.delete_appointment(s, scope=Occurrence(d(1, 15), Extent.DAY))
    s = await fresh(cozi, s.id)
    assert (s.occurrence_days, s.skipped_days) == ([d(1, 8), d(1, 22), d(1, 29)], [d(1, 15)])

    # A whole-series edit re-sends the recurrence with its exdates: the deleted day stays deleted.
    s.notes = "after a day delete"
    s = await cozi.update_appointment(s, scope=WHOLE_SERIES)
    assert (s.occurrence_days, s.skipped_days, s.notes) == (
        [d(1, 8), d(1, 22), d(1, 29)],
        [d(1, 15)],
        "after a day delete",
    )
    assert await raw_days(cozi, s.id) == ["2019-01-08", "2019-01-22", "2019-01-29"]
    assert await raw_days(cozi, s.id, 2019, 2) == ["2019-02-05", "2019-02-12"]

    # A Feb occurrence, deleted with its following ones, from the February page.
    feb = await fresh(cozi, s.id, 2019, 2)
    await cozi.delete_appointment(feb, scope=Occurrence(d(2, 5), Extent.FORWARD))
    assert await raw_days(cozi, s.id) == ["2019-01-08", "2019-01-22", "2019-01-29"]
    assert await listed(cozi, s.id, 2019, 2) is False
    # The forward delete moves rules[0].end with endDay, so a verbatim re-send cannot revive them.
    rec = (await raw_item(cozi, s.id))["itemDetails"]["recurrence"]
    assert (rec["endDay"], rec["rules"][0]["end"]) == ("2019-02-04", {"untilDay": "2019-02-04"})

    s = await fresh(cozi, s.id)
    await cozi.delete_appointment(s, scope=WHOLE_SERIES)
    assert await months_listing(cozi, s.id) == []


async def test_whole_series_edit_when_the_start_month_lists_no_day(cozi):
    # Its only January day deleted, the series is gone from the January page.
    s = await create_series(cozi, start_day=d(1, 29), until=d(2, 12))
    await cozi.delete_appointment(s, scope=Occurrence(d(1, 29), Extent.DAY))
    assert await listed(cozi, s.id) is False
    feb = await fresh(cozi, s.id, 2019, 2)
    assert (feb.start_day, feb.occurrence_days) == (d(1, 29), [d(2, 5), d(2, 12)])

    feb.notes = "edited from february"
    stored = await cozi.update_appointment(feb, scope=WHOLE_SERIES)
    assert (await raw_item(cozi, s.id, 2019, 2))["itemDetails"]["notes"] == "edited from february"
    assert await raw_days(cozi, s.id, 2019, 2) == ["2019-02-05", "2019-02-12"]
    assert await listed(cozi, s.id) is False
    assert stored == await fresh(cozi, s.id, 2019, 2)


async def test_scoped_writes_refuse_days_the_page_does_not_list(cozi):
    s = await create_series(cozi)
    before = digest(await raw_page(cozi))
    with pytest.raises(CoziValidationError, match="not an occurrence"):
        await cozi.delete_appointment(s, scope=Occurrence(d(1, 16), Extent.DAY))
    with pytest.raises(CoziValidationError, match="recurring series"):
        await cozi.delete_appointment(s)
    assert digest(await raw_page(cozi)) == before


# ------------------------------------------------------------------ search, print, smart add


async def test_search(cozi):
    token = "zq" + "".join(random.choices(string.ascii_lowercase, k=8))
    year, month = SEARCH_MONTH

    def make(day, **kw):
        return CoziAppointment(
            start_day=d(month, day, year),
            subject=f"{subject('calendar')} {token}",
            start_time=time(10),
            end_time=time(11),
            **kw,
        )

    one = track(cozi, await cozi.create_appointment(make(18)))
    rec = track(
        cozi, await cozi.create_appointment(make(4, recurrence=weekly(d(1, 4, 2022), d(1, 25, 2022))))
    )
    found = []
    try:
        for attempt in range(16):  # the index trails writes by a few seconds
            found, more = await cozi.search_calendar(token)
            if len(found) == 2:
                break
            await asyncio.sleep(2)
    except PermissionDeniedError as e:
        pytest.skip(f"calendar search needs a paid Cozi plan here: {e}")
    by_id = {a.id: a for a in found}
    assert sorted(by_id) == sorted([one.id, rec.id])
    assert more is False
    assert (by_id[one.id].start_day, by_id[one.id].start_time, by_id[one.id].occurrence_days) == (
        d(1, 18, 2022),
        time(10),
        [d(1, 18, 2022)],
    )
    assert by_id[rec.id].occurrence_days == [
        d(1, 4, 2022),
        d(1, 11, 2022),
        d(1, 18, 2022),
        d(1, 25, 2022),
    ]
    assert by_id[rec.id].is_recurring is True


async def test_print_view(cozi):
    url = f"{cozi.BASE_URL}/api/ext/2207/{cozi.account_id}/printing/calendar/week/20190107"
    headers = {**cozi.BROWSER_HEADERS, "Authorization": f"Bearer {cozi._access_token}"}
    async with cozi._session.get(url, params={"apikey": cozi.APIKEY}, headers=headers) as response:
        status, content_type = response.status, response.content_type
        body = await response.read()
    if status in (402, 403):
        pytest.skip(f"the print view needs a paid Cozi plan here (HTTP {status})")
    assert (status, content_type, body[:5]) == (200, "application/pdf", b"%PDF-")
    assert (await cozi.get_calendar_print("month", d(1, 1)))[:5] == b"%PDF-"


async def test_parse_event_stores_nothing(cozi):
    before = digest(await raw_page(cozi, 2019, 1))
    tag = subject("calendar")  # swept, should the parser ever store what it parses
    text = f"{tag} dentist January 15 at 3pm"
    try:
        event = await cozi.parse_event(text, now=datetime(2019, 1, 7, 9, tzinfo=timezone.utc))
    except PermissionDeniedError as e:
        pytest.skip(f"smart add needs a paid Cozi plan here: {e}")
    assert (event.id, event.subject, event.start_day, event.start_time, event.end_time) == (
        None,
        f"{tag} dentist",
        d(1, 15),
        time(15),
        time(16),
    )
    assert digest(await raw_page(cozi, 2019, 1)) == before


async def test_birthday_create_edit_delete(cozi):
    # With a birth year the series starts that year, so every write stays on 2019 pages.
    name = subject("calendar")
    b = await cozi.create_birthday(name, d(2, 14), birth_year=2019, reminders=[])
    cozi.made.append((b.id, d(2, 14)))
    raw = await raw_item(cozi, b.id, 2019, 2)
    assert (raw["itemType"], raw["description"], raw.get("reminders")) == (
        "birthday",
        f"{name}'s birthday",
        None,
    )
    expect_raw(raw, d(2, 14), "00:00:00", "00:00:00", 1)
    assert (raw["itemDetails"]["name"], raw["itemDetails"]["birthYear"]) == (name, 2019)
    assert raw["itemDetails"]["recurrence"]["rules"] == [
        {"frequency": "Yearly", "interval": 1, "byMonth": [2], "byMonthDay": [14]}
    ]
    assert (b.item_type, b.start_day, b.name, b.birth_year, b.is_recurring, b.is_all_day) == (
        "birthday",
        d(2, 14),
        name,
        2019,
        True,
        True,
    )
    assert b == await fresh(cozi, b.id, 2019, 2)

    b.name = subject("calendar")
    edited = await cozi.update_appointment(b)
    raw = await raw_item(cozi, b.id, 2019, 2)
    assert (raw["description"], raw["itemDetails"]["name"], raw["itemDetails"]["birthYear"]) == (
        f"{b.name}'s birthday",
        b.name,
        2019,
    )
    expect_raw(raw, d(2, 14), "00:00:00", "00:00:00", 1)
    assert edited == await fresh(cozi, b.id, 2019, 2)

    this_year = date.today().year
    assert await listed(cozi, b.id, this_year, 2) is True
    assert await cozi.delete_appointment(edited) is True
    assert await listed(cozi, b.id, 2019, 2) is False
    assert await listed(cozi, b.id, this_year, 2) is False


async def test_birthday_without_a_year_starts_in_1904(cozi):
    name = subject("calendar")
    b = await cozi.create_birthday(name, d(2, 14), reminders=[])
    cozi.made.append((b.id, d(2, 14)))  # a bare delete from a 2019 page removes the whole series
    assert (b.start_day, b.birth_year, b.occurrence_days) == (date(1904, 2, 14), None, [d(2, 14)])
    expect_raw(await raw_item(cozi, b.id, 2019, 2), date(1904, 2, 14), "00:00:00", "00:00:00", 1)


async def test_meal_create_edit_delete(cozi):
    dish = subject("calendar")
    meal = track(cozi, await cozi.create_meal(d(1, 15), "dinner", [dish, "Bread"], notes="live"))
    raw = await raw_item(cozi, meal.id)
    assert (raw["itemType"], raw["day"], raw["startTime"], raw["endTime"], raw["description"]) == (
        "meal",
        "2019-01-15",
        "18:00:00",
        "20:00:00",
        dish,
    )
    assert (
        raw["itemDetails"]["mealSlot"],
        raw["itemDetails"]["mealItems"],
        raw["itemDetails"]["notes"],
    ) == (
        "dinner",
        [{"text": dish, "recipeBoxId": None}, {"text": "Bread", "recipeBoxId": None}],
        "live",
    )
    assert (meal.meal_slot, meal.start_time, meal.is_editable) == ("dinner", time(18), False)

    meal.meal_slot = "breakfast"
    meal.meal_items = [{"text": dish}]
    edited = await cozi.update_meal(meal)
    raw = await raw_item(cozi, meal.id)
    assert (
        raw["startTime"],
        raw["endTime"],
        raw["itemDetails"]["mealSlot"],
        raw["itemDetails"]["mealItems"],
    ) == ("08:00:00", "10:00:00", "breakfast", [{"text": dish, "recipeBoxId": None}])
    assert edited == await fresh(cozi, meal.id)

    assert await cozi.delete_meal(edited) is True
    assert await listed(cozi, meal.id) is False
