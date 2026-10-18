"""Calendar endpoints with aioresponses: exact method, path and JSON body of every call."""

import logging
from datetime import date, datetime, time, timedelta, timezone

import pytest

from cozi_client.calendar import WHOLE_SERIES, CoziAppointment, Extent, Occurrence, repeat_rule
from cozi_client.exceptions import (
    CoziValidationError,
    PermissionDeniedError,
    ResponseParseError,
    WriteVerificationError,
)
from tests.conftest import FEB, JAN, SEARCH, api, sent, sent_query

UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def page(*items, days=None):
    return {"items": {i["id"]: i for i in items}, "days": days or {}, "startDate": "x", "endDate": "y"}


def raw(id, day="2019-01-15", subject="Dentist", start="10:00:00", end="11:00:00", **kw):
    return {
        "id": id,
        "day": day,
        "description": subject,
        "startTime": start,
        "endTime": end,
        "itemType": "appointment",
        **kw,
    }


def series_raw(id="s1", day="2019-01-08", **details):
    rule = {"frequency": "Weekly", "interval": 1, "byDay": ["TU"], "end": {"untilDay": "2019-02-12"}}
    return raw(
        id,
        day,
        "Gym",
        "07:00:00",
        "07:30:00",
        itemVersion=7,
        itemDetails={
            "recurrenceStartDay": day,
            "recurrence": {"rules": [rule], "endDay": "2019-02-12", **details},
        },
    )


SERIES_DAYS = {d: [{"id": "s1"}] for d in ("2019-01-08", "2019-01-15", "2019-01-22", "2019-01-29")}


def series(**details):
    return CoziAppointment.model_validate(
        {**series_raw(**details), "occurrence_days": [date(2019, 1, d) for d in (8, 15, 22, 29)]}
    )


def feb_series():
    appt = series()
    appt.occurrence_days = [date(2019, 2, 5), date(2019, 2, 12)]
    return appt


def unlisted_series():
    appt = series()
    appt.occurrence_days = []
    return appt


def startless_series():
    # Built by hand, or read from a payload without itemDetails.recurrenceStartDay.
    appt = series()
    appt.recurrence_start_day = None
    return appt


def one_off(**kw):
    return CoziAppointment.model_validate({**raw("a1", itemVersion=3), **kw})


DETAILS_1000 = {
    "startTime": "10:00",
    "endTime": "11:00",
    "dateSpan": 0,
    "attendeeSet": [],
    "location": None,
    "notes": None,
}
GYM_DETAILS = {
    "startTime": "07:00",
    "endTime": "07:30",
    "dateSpan": 0,
    "attendeeSet": [],
    "location": None,
    "notes": None,
    "subject": "Gym",
}


def posted(mocked, path=JAN):
    return [c["json"] for c in sent(mocked, "POST", path)]


class TestRead:
    async def test_get_calendar(self, client, mocked, caplog):
        days = {
            **SERIES_DAYS,
            "2019-01-31": [{"id": "t1", "multiday": {"startDay": "2019-01-31", "endDay": "2019-02-01"}}],
        }
        mocked.get(
            api(JAN),
            payload=page(
                series_raw(),
                raw("t1", "2019-01-31", dateSpan=1),
                {"id": "bad", "day": "nope"},
                days=days,
            ),
        )
        with caplog.at_level(logging.WARNING, logger="cozi_client"):
            items = await client.get_calendar(2019, 1)
        assert [a.id for a in items] == ["s1", "t1"]
        assert items[0].occurrence_days == [date(2019, 1, d) for d in (8, 15, 22, 29)]
        assert items[1].occurrence_days == [date(2019, 1, 31)]
        assert sent(mocked, "GET", JAN)[0].get("json") is None
        assert "Skipping calendar item bad" in caplog.text

    async def test_get_calendar_validates_period_before_io(self, client, mocked):
        with pytest.raises(CoziValidationError):
            await client.get_calendar(2019, 13)
        assert sent(mocked, "GET", "/2004/{acct}/calendar/2019/13") == []

    async def test_not_a_page_is_a_parse_error(self, client, mocked):
        mocked.get(api(JAN), payload=[raw("a1")])
        with pytest.raises(ResponseParseError):
            await client.get_calendar(2019, 1)

    async def test_search(self, client, mocked):
        key = f"{UUID}-a"
        mocked.get(
            api(SEARCH),
            payload={
                "items": {
                    key: {"highlight": {"description": "x"}, "item": raw(key, "2022-01-18")},
                    "other-b": raw("other-b", "2022-01-19"),
                },
                "days": {"2022-01-18": [{"id": key}]},
                "selectedDay": "2022-01-18",
                "hasNext": True,
            },
        )
        found, more = await client.search_calendar("  a&b #c+d ")
        assert [(a.id, a.occurrence_days) for a in found] == [
            (UUID, [date(2022, 1, 18)]),
            ("other-b", []),
        ]
        assert more is True
        assert sent_query(mocked, "GET", SEARCH)[0]["q"] == "a&b #c+d"

    async def test_search_refusals(self, client, mocked):
        with pytest.raises(CoziValidationError):
            await client.search_calendar("   ")
        assert sent(mocked, "GET", SEARCH) == []
        mocked.get(api(SEARCH), status=403, payload={"error": "upgrade"})
        with pytest.raises(PermissionDeniedError):
            await client.search_calendar("x")

    async def test_print(self, client, mocked):
        path = "/2207/{acct}/printing/calendar/week/20190107"
        mocked.get(api(path), body=b"%PDF-1.4", content_type="application/pdf")
        assert await client.get_calendar_print("week", date(2019, 1, 7)) == b"%PDF-1.4"
        assert len(sent(mocked, "GET", path)) == 1
        with pytest.raises(CoziValidationError):
            await client.get_calendar_print("../x", date(2019, 1, 7))

    async def test_parse_event(self, client, mocked):
        path = "/2307/{acct}/calendar/parse/"
        mocked.post(
            api(path),
            payload={
                "parsedEvent": {
                    "id": "minted",
                    "description": "Dentist",
                    "day": None,
                    "startTime": "15:00:00",
                    "endTime": None,
                    "householdMembers": ["p1"],
                }
            },
        )
        now = datetime(2019, 1, 7, 9, 30, 15, 123, tzinfo=timezone(timedelta(hours=-8)))
        appt = await client.parse_event("dentist 3pm", now=now)
        assert sent(mocked, "POST", path)[0]["json"] == {
            "text": "dentist 3pm",
            "now": "2019-01-07T09:30:15-08:00",
        }
        assert (
            appt.id,
            appt.subject,
            appt.start_day,
            appt.start_time,
            appt.end_time,
            appt.attendees,
        ) == (None, "Dentist", date(2019, 1, 7), time(15), time(0), ["p1"])

    async def test_parse_event_default_now_is_local_with_offset(self, client, mocked):
        path = "/2307/{acct}/calendar/parse/"
        mocked.post(api(path), payload={"parsedEvent": {"description": "x", "day": "2019-01-07"}})
        appt = await client.parse_event("x")
        now = datetime.fromisoformat(sent(mocked, "POST", path)[0]["json"]["now"])
        assert now.tzinfo is not None and now.microsecond == 0
        assert appt.is_all_day


class TestCreate:
    async def test_create_series_with_reminders_and_notify(self, client, mocked):
        mocked.get(api(JAN), payload=page(raw("old")))
        mocked.post(api(JAN), payload=page(raw("old"), series_raw("new")))
        appt = CoziAppointment(
            start_day=date(2019, 1, 8),
            subject="Gym",
            start_time=time(7),
            end_time=time(7, 30),
            recurrence=repeat_rule("weekly", start_day=date(2019, 1, 8), until=date(2019, 2, 12)),
            reminders=[1440, 30],
        )
        stored = await client.create_appointment(appt, notify=["p1"])
        assert posted(mocked) == [
            [
                {
                    "itemType": "appointment",
                    "notifyPersons": ["p1"],
                    "create": {
                        "startDay": "2019-01-08",
                        "recurrence": {
                            "rules": [
                                {
                                    "frequency": "Weekly",
                                    "interval": 1,
                                    "byDay": ["TU"],
                                    "end": {"untilDay": "2019-02-12"},
                                }
                            ]
                        },
                        "reminders": [{"minutesBefore": 1440}, {"minutesBefore": 30}],
                        "details": GYM_DETAILS,
                    },
                }
            ]
        ]
        assert (stored.id, stored.item_version, stored.end_day) == ("new", 7, date(2019, 2, 12))
        assert appt.id is None  # the input is not mutated

    async def test_create_all_day_sends_midnight_and_one_day(self, client, mocked):
        mocked.get(api(JAN), payload=page())
        mocked.post(
            api(JAN), payload=page(raw("n", "2019-01-31", "Trip", "00:00:00", "00:00:00", dateSpan=1))
        )
        stored = await client.create_appointment(
            CoziAppointment(start_day=date(2019, 1, 31), subject="Trip")
        )
        assert posted(mocked) == [
            [
                {
                    "itemType": "appointment",
                    "create": {
                        "startDay": "2019-01-31",
                        "details": {
                            "startTime": "00:00",
                            "endTime": "00:00",
                            "dateSpan": 1,
                            "attendeeSet": [],
                            "location": None,
                            "notes": None,
                            "subject": "Trip",
                        },
                    },
                }
            ]
        ]
        assert (stored.is_all_day, stored.date_span) == (True, 1)

    async def test_create_narrows_two_new_items_by_subject(self, client, mocked):
        mocked.get(api(JAN), payload=page())
        mocked.post(api(JAN), payload=page(raw("n1"), raw("n2", subject="Other")))
        stored = await client.create_appointment(
            CoziAppointment(
                start_day=date(2019, 1, 15), subject="Dentist", start_time=time(10), end_time=time(11)
            )
        )
        assert stored.id == "n1"

    @pytest.mark.parametrize("after", [page(raw("old")), page(raw("old"), raw("n1"), raw("n2"))])
    async def test_create_without_exactly_one_new_item_raises(self, client, mocked, after):
        mocked.get(api(JAN), payload=page(raw("old")))
        mocked.post(api(JAN), payload=after)
        with pytest.raises(WriteVerificationError):
            await client.create_appointment(
                CoziAppointment(
                    start_day=date(2019, 1, 15),
                    subject="Dentist",
                    start_time=time(10),
                    end_time=time(11),
                )
            )

    async def test_create_rejected(self, client, mocked):
        mocked.get(api(JAN), payload=page())
        mocked.post(
            api(JAN),
            payload={
                **page(),
                "rejectedItems": [
                    {"operation": "create", "error": "start_time and end_time are required"}
                ],
            },
        )
        with pytest.raises(WriteVerificationError, match="start_time and end_time are required"):
            await client.create_appointment(CoziAppointment(start_day=date(2019, 1, 15), subject="x"))

    @pytest.mark.parametrize(
        "appt",
        [
            CoziAppointment(start_day=date(2019, 1, 15), subject="  "),
            CoziAppointment(start_day=date(2019, 1, 15), subject="x", start_time=time(10)),
            CoziAppointment(
                start_day=date(2019, 1, 15),
                subject="x",
                date_span=2,
                recurrence=repeat_rule("daily", start_day=date(2019, 1, 15)),
            ),
            # A meal or birthday read back would become a plain appointment.
            CoziAppointment(start_day=date(2019, 1, 15), subject="Soup", item_type="meal"),
            CoziAppointment(
                start_day=date(2019, 1, 15), subject="Ann's birthday", item_type="birthday"
            ),
        ],
    )
    async def test_create_guards_before_io(self, client, mocked, appt):
        with pytest.raises(CoziValidationError):
            await client.create_appointment(appt)
        assert sent(mocked, "GET", JAN) == [] and posted(mocked) == []

    async def test_create_refuses_bad_notify_ids(self, client, mocked):
        with pytest.raises(CoziValidationError):
            await client.create_appointment(one_off(), notify=["../x"])
        with pytest.raises(CoziValidationError):
            await client.create_appointment(one_off(), notify="p1")
        assert posted(mocked) == []


class TestUpdate:
    async def test_one_off_full_replace_returns_stored(self, client, mocked):
        appt = one_off(itemDetails={"notes": "old", "location": "L"}, reminders=[{"minutesBefore": 60}])
        appt.notes = "new"
        mocked.post(
            api(JAN),
            payload=page(raw("a1", itemVersion=4, itemDetails={"notes": "new", "location": "L"})),
        )
        stored = await client.update_appointment(appt, notify=["p1"])
        assert posted(mocked) == [
            [
                {
                    "itemType": "appointment",
                    "notifyPersons": ["p1"],
                    "edit": {
                        "id": "a1",
                        "startDay": "2019-01-15",
                        "reminders": [{"minutesBefore": 60}],
                        "details": {
                            **DETAILS_1000,
                            "location": "L",
                            "notes": "new",
                            "subject": "Dentist",
                        },
                    },
                }
            ]
        ]
        assert (stored.notes, stored.item_version) == ("new", 4)

    async def test_cleared_reminders_are_sent_empty(self, client, mocked):
        appt = one_off(reminders=[{"minutesBefore": 60}])
        appt.reminders = []
        mocked.post(api(JAN), payload=page(raw("a1")))
        await client.update_appointment(appt)
        assert posted(mocked)[0][0]["edit"]["reminders"] == []

    async def test_all_day_read_back_edits_with_midnight(self, client, mocked):
        appt = one_off(startTime="00:00:00", endTime="00:00:00", dateSpan=1)
        mocked.post(api(JAN), payload=page(raw("a1")))
        await client.update_appointment(appt)
        details = posted(mocked)[0][0]["edit"]["details"]
        assert (details["startTime"], details["endTime"], details["dateSpan"]) == ("00:00", "00:00", 1)

    async def test_moved_to_another_month_posts_to_that_page(self, client, mocked):
        appt = one_off()
        appt.start_day = date(2019, 2, 10)
        mocked.post(api(FEB), payload=page(raw("a1", "2019-02-10")))
        assert (await client.update_appointment(appt)).start_day == date(2019, 2, 10)
        assert posted(mocked, JAN) == [] and len(posted(mocked, FEB)) == 1

    async def test_missing_from_the_answer_raises(self, client, mocked):
        mocked.post(api(JAN), payload=page())
        with pytest.raises(WriteVerificationError):
            await client.update_appointment(one_off())

    async def test_whole_series(self, client, mocked):
        appt = series()
        mocked.post(api(JAN), payload=page(series_raw(), days=SERIES_DAYS))
        stored = await client.update_appointment(appt, scope=WHOLE_SERIES)
        assert posted(mocked) == [
            [
                {
                    "itemType": "appointment",
                    "edit": {
                        "id": "s1",
                        "startDay": "2019-01-08",
                        "recurrence": appt.recurrence,
                        "details": GYM_DETAILS,
                    },
                }
            ]
        ]
        assert stored.occurrence_days == appt.occurrence_days

    async def test_whole_series_gone_from_its_start_month_is_read_from_its_page(self, client, mocked):
        # Every January day of the series was deleted, so January no longer lists it (live).
        appt = feb_series()
        appt.notes = "n"
        mocked.post(api(JAN), payload=page(raw("other")))
        mocked.get(
            api(FEB),
            payload=page(
                series_raw(), days={"2019-02-05": [{"id": "s1"}], "2019-02-12": [{"id": "s1"}]}
            ),
        )
        stored = await client.update_appointment(appt, scope=WHOLE_SERIES)
        assert posted(mocked) == [
            [
                {
                    "itemType": "appointment",
                    "edit": {
                        "id": "s1",
                        "startDay": "2019-01-08",
                        "recurrence": appt.recurrence,
                        "details": {**GYM_DETAILS, "notes": "n"},
                    },
                }
            ]
        ]
        assert (stored.id, stored.occurrence_days) == ("s1", [date(2019, 2, 5), date(2019, 2, 12)])
        assert len(sent(mocked, "GET", FEB)) == 1

    async def test_whole_series_missing_from_both_pages_raises(self, client, mocked):
        mocked.post(api(JAN), payload=page())
        mocked.get(api(FEB), payload=page())
        with pytest.raises(WriteVerificationError, match="not on the page"):
            await client.update_appointment(feb_series(), scope=WHOLE_SERIES)

    async def test_day_scope_returns_the_stand_in(self, client, mocked):
        appt = series()
        appt.start_day = date(2019, 1, 16)
        exdates = [{"date": "2019-01-15T07:00:00", "standinApptId": "x2"}]
        mocked.post(
            api(JAN),
            payload=page(
                series_raw(exdates=exdates), raw("x2", "2019-01-16", "Gym", "07:00:00", "07:30:00")
            ),
        )
        stored = await client.update_appointment(appt, scope=Occurrence(date(2019, 1, 15), Extent.DAY))
        assert posted(mocked) == [
            [
                {
                    "itemType": "appointment",
                    "selectedDay": "2019-01-15",
                    "extent": "day",
                    "edit": {
                        "id": "s1",
                        "startDay": "2019-01-16",
                        "recurrence": appt.recurrence,
                        "details": GYM_DETAILS,
                    },
                }
            ]
        ]
        assert stored.id == "x2"

    async def test_day_scope_series_gone_from_page_matches_the_look_alike(self, client, mocked):
        # The series' only January occurrence became the stand-in, so January no longer lists it.
        appt = series()
        appt.occurrence_days = [date(2019, 1, 29)]
        appt.start_day = date(2019, 1, 29)
        mocked.post(
            api(JAN),
            payload=page(
                raw("x2", "2019-01-29", "Gym", "07:00:00", "07:30:00"),
                raw("other", "2019-01-29", "Gym", "08:00:00", "08:30:00"),
            ),
        )
        stored = await client.update_appointment(appt, scope=Occurrence(date(2019, 1, 29), Extent.DAY))
        assert stored.id == "x2"

    async def test_day_scope_reads_the_occurrence_page_for_the_exdate(self, client, mocked):
        # Occurrence Feb 5 moved to Jan 31: the series is gone from January; its exdate is on February.
        appt = series()
        appt.occurrence_days = [date(2019, 2, 5), date(2019, 2, 12)]
        appt.start_day = date(2019, 1, 31)
        exdates = [{"date": "2019-02-05T07:00:00", "standinApptId": "x2"}]
        mocked.post(
            api(JAN),
            payload=page(
                raw("x2", "2019-01-31", "Gym", "07:00:00", "07:30:00"),
                raw("twin", "2019-01-31", "Gym", "07:00:00", "07:30:00"),
            ),
        )
        mocked.get(api(FEB), payload=page(series_raw(exdates=exdates)))
        stored = await client.update_appointment(appt, scope=Occurrence(date(2019, 2, 5), Extent.DAY))
        assert stored.id == "x2"
        assert len(sent(mocked, "GET", FEB)) == 1

    async def test_day_scope_twin_look_alikes_raise(self, client, mocked):
        appt = series()
        appt.occurrence_days = [date(2019, 1, 29)]
        appt.start_day = date(2019, 1, 29)
        twin = raw("x2", "2019-01-29", "Gym", "07:00:00", "07:30:00")
        mocked.post(api(JAN), payload=page(twin, {**twin, "id": "x3"}))
        with pytest.raises(WriteVerificationError, match="cannot be told apart"):
            await client.update_appointment(appt, scope=Occurrence(date(2019, 1, 29), Extent.DAY))

    async def test_day_scope_series_listed_without_stand_in_raises(self, client, mocked):
        appt = series()
        appt.start_day = date(2019, 1, 15)
        mocked.post(
            api(JAN), payload=page(series_raw(), raw("x2", "2019-01-15", "Gym", "07:00:00", "07:30:00"))
        )
        with pytest.raises(WriteVerificationError, match="could not be found"):
            await client.update_appointment(appt, scope=Occurrence(date(2019, 1, 15), Extent.DAY))

    async def test_forward_scope_returns_the_new_series(self, client, mocked):
        appt = series()
        appt.start_day = date(2019, 1, 22)
        decoy = raw("decoy", "2019-01-22", "Gym", "07:00:00", "07:30:00")
        mocked.post(api(JAN), payload=page(series_raw(), series_raw("s2", "2019-01-22"), decoy))
        stored = await client.update_appointment(
            appt, scope=Occurrence(date(2019, 1, 22), Extent.FORWARD)
        )
        assert (
            posted(mocked)[0][0]["selectedDay"] == "2019-01-22"
            and posted(mocked)[0][0]["extent"] == "forward"
        )
        assert stored.id == "s2"

    async def test_forward_from_the_first_occurrence_edits_in_place(self, client, mocked):
        appt = series()
        appt.start_time, appt.end_time = time(8), time(8, 30)
        mocked.post(api(JAN), payload=page(series_raw(), days=SERIES_DAYS))
        stored = await client.update_appointment(
            appt, scope=Occurrence(date(2019, 1, 8), Extent.FORWARD)
        )
        assert posted(mocked)[0][0]["extent"] == "forward"
        assert stored.id == "s1"

    async def test_forward_split_with_the_series_start_unknown_returns_the_new_series(
        self, client, mocked
    ):
        # start_day was moved to the occurrence, so it cannot stand in for the series start.
        appt = startless_series()
        appt.start_day = date(2019, 1, 22)
        appt.start_time, appt.end_time = time(9), time(9, 30)
        split = {**series_raw("s2", "2019-01-22"), "startTime": "09:00:00", "endTime": "09:30:00"}
        mocked.post(api(JAN), payload=page(series_raw(), split))
        stored = await client.update_appointment(
            appt, scope=Occurrence(date(2019, 1, 22), Extent.FORWARD)
        )
        assert (stored.id, stored.start_day, stored.start_time) == (
            "s2",
            date(2019, 1, 22),
            time(9),
        )

    async def test_forward_from_the_first_occurrence_with_the_series_start_unknown(
        self, client, mocked
    ):
        appt = startless_series()
        appt.start_time, appt.end_time = time(8), time(8, 30)
        edited = {**series_raw(), "startTime": "08:00:00", "endTime": "08:30:00"}
        mocked.post(api(JAN), payload=page(edited, days=SERIES_DAYS))
        stored = await client.update_appointment(
            appt, scope=Occurrence(date(2019, 1, 8), Extent.FORWARD)
        )
        assert (stored.id, stored.start_time) == ("s1", time(8))

    @pytest.mark.parametrize(
        "make, scope, match",
        [
            (series, None, "recurring series"),
            (series, Occurrence(date(2019, 1, 16), Extent.DAY), "not an occurrence"),
            (series, Occurrence(date(2019, 1, 15), "day"), "Invalid occurrence"),
            (series, Occurrence(datetime(2019, 1, 15), Extent.DAY), "Invalid occurrence"),
            # No day map to check against: the series' own bounds still apply.
            (unlisted_series, Occurrence(date(2019, 2, 19), Extent.DAY), "outside the series"),
            (unlisted_series, Occurrence(date(2019, 1, 1), Extent.FORWARD), "outside the series"),
            (series, "day", "scope must be"),
            (one_off, Occurrence(date(2019, 1, 15), Extent.DAY), "not recurring"),
            # start_day left on another occurrence: the stand-in would land on Jan 8.
            (series, Occurrence(date(2019, 1, 15), Extent.DAY), "another occurrence"),
            # Read from February, start_day still on the series start.
            (feb_series, Occurrence(date(2019, 2, 5), Extent.FORWARD), "another occurrence"),
            (lambda: one_off(itemSource="US Holidays"), None, "subscribed or read-only"),
            (lambda: one_off(itemDetails={"readOnly": True}), None, "subscribed or read-only"),
            (lambda: one_off(itemType="meal"), None, "use update_meal"),
            (lambda: one_off(itemType="empty"), None, "cannot change"),
            (lambda: one_off(id="../x"), None, "Invalid appointment id"),
            (lambda: one_off(id=None), None, "Invalid appointment id"),
        ],
    )
    async def test_guards_before_io(self, client, mocked, make, scope, match):
        with pytest.raises(CoziValidationError, match=match):
            await client.update_appointment(make(), scope=scope)
        assert posted(mocked) == []

    async def test_never_sends_item_version(self, client, mocked):
        mocked.post(api(JAN), payload=page(raw("a1")))
        await client.update_appointment(one_off())
        op = posted(mocked)[0][0]
        assert "itemVersion" not in op["edit"] and "version" not in op["edit"]

    async def test_rejected_edit(self, client, mocked):
        mocked.post(
            api(JAN),
            payload={
                **page(raw("a1")),
                "rejectedItems": [{"operation": "edit", "id": "a1", "error": "nope"}],
            },
        )
        with pytest.raises(WriteVerificationError, match="nope"):
            await client.update_appointment(one_off())

    async def test_rejection_of_another_item_is_not_ours(self, client, mocked):
        mocked.post(
            api(JAN),
            payload={
                **page(raw("a1")),
                "rejectedItems": [{"operation": "edit", "id": "zz", "error": "nope"}],
            },
        )
        assert (await client.update_appointment(one_off())).id == "a1"


class TestDelete:
    async def test_one_off_sends_version(self, client, mocked):
        mocked.post(api(JAN), payload=page())
        await client.delete_appointment(one_off(), notify=["p1"])
        assert posted(mocked) == [
            [{"itemType": "appointment", "delete": {"id": "a1", "version": 3}, "notifyPersons": ["p1"]}]
        ]

    async def test_without_a_version(self, client, mocked):
        mocked.post(api(JAN), payload=page())
        await client.delete_appointment(
            CoziAppointment(id="a1", start_day=date(2019, 1, 15), subject="x")
        )
        assert posted(mocked) == [[{"itemType": "appointment", "delete": {"id": "a1"}}]]

    async def test_occurrence_goes_to_its_page(self, client, mocked):
        appt = series()
        appt.occurrence_days = [date(2019, 2, 5), date(2019, 2, 12)]
        mocked.post(api(FEB), payload=page())
        await client.delete_appointment(appt, scope=Occurrence(date(2019, 2, 5), Extent.FORWARD))
        assert posted(mocked, FEB) == [
            [
                {
                    "itemType": "appointment",
                    "delete": {"id": "s1", "version": 7},
                    "selectedDay": "2019-02-05",
                    "extent": "forward",
                }
            ]
        ]

    async def test_whole_series(self, client, mocked):
        mocked.post(api(JAN), payload=page())
        await client.delete_appointment(series(), scope=WHOLE_SERIES)
        assert posted(mocked) == [[{"itemType": "appointment", "delete": {"id": "s1", "version": 7}}]]

    async def test_series_needs_a_scope(self, client, mocked):
        with pytest.raises(CoziValidationError, match="recurring series"):
            await client.delete_appointment(series())
        with pytest.raises(CoziValidationError, match="not an occurrence"):
            await client.delete_appointment(series(), scope=Occurrence(date(2019, 1, 9), Extent.DAY))
        with pytest.raises(CoziValidationError, match="use update_meal or delete_meal"):
            await client.delete_appointment(one_off(itemType="meal"))
        with pytest.raises(CoziValidationError, match="outside the series"):
            await client.delete_appointment(
                unlisted_series(), scope=Occurrence(date(2019, 2, 19), Extent.FORWARD)
            )
        assert posted(mocked) == [] and posted(mocked, FEB) == []

    async def test_stale_version_is_rejected(self, client, mocked):
        error = "Operation rejected due to version mismatch. Detail: a1"
        mocked.post(
            api(JAN),
            payload={
                **page(raw("a1")),
                "rejectedItems": [
                    {"operation": "delete", "itemType": "appointment", "id": "a1", "error": error}
                ],
            },
        )
        with pytest.raises(WriteVerificationError, match="version mismatch"):
            await client.delete_appointment(one_off())


def birthday_raw(id="b1", day="2019-02-14"):
    return {
        "id": id,
        "day": day,
        "description": "Ann's Birthday",
        "startTime": "00:00:00",
        "endTime": "00:00:00",
        "dateSpan": 1,
        "itemType": "birthday",
        "itemVersion": 2,
        "itemDetails": {
            "name": "Ann",
            "birthYear": 1990,
            "recurrence": {"rules": [{"frequency": "Yearly"}]},
        },
    }


class TestBirthday:
    async def test_create(self, client, mocked):
        mocked.get(api(FEB), payload=page())
        mocked.post(api(FEB), payload=page(birthday_raw()))
        stored = await client.create_birthday("Ann", date(2019, 2, 14), birth_year=1990)
        assert posted(mocked, FEB) == [
            [
                {
                    "itemType": "birthday",
                    "create": {
                        "startDay": "2019-02-14",
                        "reminders": [{"minutesBefore": 10080}, {"minutesBefore": 1440}],
                        "recurrence": {
                            "rules": [
                                {
                                    "byMonth": [2],
                                    "byMonthDay": [14],
                                    "interval": 1,
                                    "frequency": "Yearly",
                                }
                            ]
                        },
                        "itemType": "birthday",
                        "details": {
                            "startTime": "00:00",
                            "endTime": "00:00",
                            "dateSpan": 1,
                            "attendeeSet": [],
                            "location": None,
                            "notes": None,
                            "name": "Ann",
                            "birthYear": 1990,
                        },
                    },
                }
            ]
        ]
        assert (stored.id, stored.name, stored.birth_year) == ("b1", "Ann", 1990)

    async def test_leap_day_is_the_last_day_of_february(self, client, mocked):
        path = "/2004/{acct}/calendar/2020/2"
        mocked.get(api(path), payload=page())
        mocked.post(api(path), payload=page(birthday_raw(day="2020-02-29")))
        await client.create_birthday("Ann", date(2020, 2, 29), reminders=[])
        create = posted(mocked, path)[0][0]["create"]
        assert create["recurrence"]["rules"][0]["byMonthDay"] == [-1]
        assert (create["reminders"], create["details"]["birthYear"]) == ([], None)

    async def test_create_takes_a_day_the_model_accepts_and_no_reminders(self, client, mocked):
        mocked.get(api(FEB), payload=page())
        mocked.post(api(FEB), payload=page(birthday_raw()))
        await client.create_birthday("Ann", "2019-02-14", reminders=None)
        create = posted(mocked, FEB)[0][0]["create"]
        assert create["startDay"] == "2019-02-14" and "reminders" not in create

    async def test_edit_rebuilds_the_rule_and_sends_no_scope(self, client, mocked):
        b = CoziAppointment.model_validate(birthday_raw())
        b.name, b.start_day = "Anne", date(2019, 2, 15)
        mocked.post(api(FEB), payload=page(birthday_raw()))
        await client.update_appointment(b, scope=WHOLE_SERIES)
        assert posted(mocked, FEB) == [
            [
                {
                    "itemType": "birthday",
                    "edit": {
                        "id": "b1",
                        "startDay": "2019-02-15",
                        "recurrence": {
                            "rules": [
                                {
                                    "byMonth": [2],
                                    "byMonthDay": [15],
                                    "interval": 1,
                                    "frequency": "Yearly",
                                }
                            ]
                        },
                        "itemType": "birthday",
                        "details": {
                            "startTime": "00:00",
                            "endTime": "00:00",
                            "dateSpan": 1,
                            "attendeeSet": [],
                            "location": None,
                            "notes": None,
                            "name": "Anne",
                            "birthYear": 1990,
                        },
                    },
                }
            ]
        ]

    async def test_delete_forward_from_its_day(self, client, mocked):
        mocked.post(api(FEB), payload=page())
        await client.delete_appointment(CoziAppointment.model_validate(birthday_raw()))
        assert posted(mocked, FEB) == [
            [
                {
                    "itemType": "appointment",
                    "delete": {"id": "b1", "version": 2},
                    "selectedDay": "2019-02-14",
                    "extent": "forward",
                }
            ]
        ]

    async def test_no_scope_or_notify(self, client, mocked):
        b = CoziAppointment.model_validate(birthday_raw())
        with pytest.raises(CoziValidationError):
            await client.update_appointment(b, scope=Occurrence(date(2019, 2, 14), Extent.DAY))
        with pytest.raises(CoziValidationError):
            await client.delete_appointment(b, notify=["p1"])
        with pytest.raises(CoziValidationError):
            await client.create_birthday(" ", date(2019, 2, 14))
        # Cozi would start the series in 2020, off the 2019 page the create is read back from.
        with pytest.raises(CoziValidationError, match="birth_year 2020"):
            await client.create_birthday("Ann", date(2019, 2, 14), birth_year=2020)
        with pytest.raises(CoziValidationError, match="birth_year 2020"):
            await client.create_birthday("Ann", "2019-02-14", birth_year="2020")
        with pytest.raises(CoziValidationError, match="Invalid calendar item"):
            await client.create_birthday("Ann", None)
        with pytest.raises(CoziValidationError, match="Invalid calendar item"):
            await client.create_birthday("Ann", date(2019, 2, 14), reminders="1440")  # not [1,4,4,0]
        assert sent(mocked, "GET", FEB) == [] and posted(mocked, FEB) == []


def meal_raw(id="m1", items=({"text": "Soup"}, {"text": "Bread", "recipeBoxId": "r1"})):
    return {
        "id": id,
        "day": "2019-01-15",
        "description": "Soup",
        "startTime": "18:00:00",
        "endTime": "20:00:00",
        "itemType": "meal",
        "itemVersion": 5,
        "itemDetails": {"id": "md1", "mealSlot": "dinner", "mealItems": list(items), "notes": ""},
    }


class TestMeal:
    async def test_create(self, client, mocked):
        mocked.get(api(JAN), payload=page())
        mocked.post(api(JAN), payload=page(meal_raw()))
        stored = await client.create_meal(
            date(2019, 1, 15), "Dinner", ["Soup", ("Bread", "r1"), ("Salad", None)], notes="n"
        )
        assert posted(mocked) == [
            [
                {
                    "itemType": "meal",
                    "create": {
                        "startDay": "2019-01-15",
                        "itemType": "meal",
                        "details": {
                            "subject": "Soup",
                            "startTime": "18:00:00",
                            "endTime": "20:00:00",
                            "mealItems": [
                                {"text": "Soup"},
                                {"text": "Bread", "recipeBoxId": "r1"},
                                {"text": "Salad"},
                            ],
                            "mealSlot": "dinner",
                            "notes": "n",
                        },
                    },
                }
            ]
        ]
        assert (stored.id, stored.meal_slot, stored.start_time) == ("m1", "dinner", time(18))

    @pytest.mark.parametrize("day", ["2019-01-15", datetime(2019, 1, 15)])
    async def test_create_matches_on_the_day_the_model_accepts(self, client, mocked, day):
        mocked.get(api(JAN), payload=page())
        mocked.post(api(JAN), payload=page(raw("other"), meal_raw()))  # two new: the match decides
        stored = await client.create_meal(day, "dinner", ["Soup"])
        assert stored.id == "m1"
        assert posted(mocked)[0][0]["create"]["startDay"] == "2019-01-15"

    async def test_update(self, client, mocked):
        meal = CoziAppointment.model_validate(meal_raw())
        meal.meal_slot = "breakfast"
        meal.meal_items = [{"text": "Eggs", "mealItemKey": "k"}]
        mocked.post(api(JAN), payload=page(meal_raw()))
        await client.update_meal(meal)
        assert posted(mocked) == [
            [
                {
                    "itemType": "meal",
                    "edit": {
                        "id": "m1",
                        "startDay": "2019-01-15",
                        "itemType": "meal",
                        "details": {
                            "subject": "Eggs",
                            "startTime": "8:00:00",
                            "endTime": "10:00:00",
                            "mealItems": [{"text": "Eggs"}],
                            "mealSlot": "breakfast",
                            "notes": "",
                        },
                    },
                }
            ]
        ]

    async def test_delete(self, client, mocked):
        mocked.post(api(JAN), payload=page())
        await client.delete_meal(CoziAppointment.model_validate(meal_raw()))
        assert posted(mocked) == [
            [
                {
                    "itemType": "appointment",
                    "delete": {"id": "m1", "version": 5},
                    "selectedDay": "2019-01-15",
                    "extent": "day",
                }
            ]
        ]

    async def test_guards(self, client, mocked):
        with pytest.raises(CoziValidationError):
            await client.create_meal(date(2019, 1, 15), "brunch", ["x"])
        with pytest.raises(CoziValidationError):
            await client.create_meal(date(2019, 1, 15), "dinner", [])
        with pytest.raises(CoziValidationError, match="not a single string"):
            await client.create_meal(date(2019, 1, 15), "dinner", "Soup")  # would be 4 dishes
        with pytest.raises(CoziValidationError, match="pair"):
            await client.create_meal(date(2019, 1, 15), "dinner", [("Soup",)])
        with pytest.raises(CoziValidationError, match="Invalid calendar item"):
            await client.create_meal(date(2019, 1, 15), 5, ["Soup"])
        with pytest.raises(CoziValidationError):
            await client.update_meal(one_off())
        with pytest.raises(CoziValidationError):
            await client.delete_meal(one_off())
        assert sent(mocked, "GET", JAN) == [] and posted(mocked) == []
