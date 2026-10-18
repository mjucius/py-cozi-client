"""Calendar over a real socket, against tests/stub_cozi.py (exact paths, apikey and bearer enforced)."""

from datetime import date, datetime, time, timezone

from cozi_client.calendar import CoziAppointment, Extent, Occurrence
from tests.conftest import FEB, JAN, SEARCH

PRINT = "/2207/{acct}/printing/calendar/week/20190107"
PARSE = "/2307/{acct}/calendar/parse/"

STORED = {
    "id": "n1",
    "day": "2019-01-15",
    "description": "Dentist",
    "startTime": "10:00:00",
    "endTime": "11:00:00",
    "itemType": "appointment",
    "itemVersion": 1,
    "itemDetails": {"id": "n1"},
}


async def test_create_reads_the_page_then_posts_one_operation(stub):
    stub.route("GET", JAN, json={"items": {}, "days": {}})
    stub.route("POST", JAN, json={"items": {"n1": STORED}, "days": {"2019-01-15": [{"id": "n1"}]}})
    async with stub.client() as cozi:
        stored = await cozi.create_appointment(
            CoziAppointment(
                start_day=date(2019, 1, 15), subject="Dentist", start_time=time(10), end_time=time(11)
            )
        )
    assert (stored.id, stored.occurrence_days) == ("n1", [date(2019, 1, 15)])
    assert len(stub.sent("GET", JAN)) == 1
    post = stub.sent("POST", JAN)[0]
    assert post["content_type"] == "application/json"
    assert post["json"] == [
        {
            "itemType": "appointment",
            "create": {
                "startDay": "2019-01-15",
                "details": {
                    "startTime": "10:00",
                    "endTime": "11:00",
                    "dateSpan": 0,
                    "attendeeSet": [],
                    "location": None,
                    "notes": None,
                    "subject": "Dentist",
                },
            },
        }
    ]


async def test_occurrence_delete_goes_to_the_occurrence_page(stub):
    stub.route("POST", FEB, json={"items": {}, "days": {}})
    series = CoziAppointment.model_validate(
        {
            "id": "s1",
            "day": "2019-01-08",
            "itemVersion": 9,
            "itemDetails": {"recurrence": {"rules": [{"frequency": "Weekly"}]}},
            "occurrence_days": [date(2019, 2, 5)],
        }
    )
    async with stub.client() as cozi:
        await cozi.delete_appointment(series, scope=Occurrence(date(2019, 2, 5), Extent.DAY))
    assert stub.sent("POST", FEB)[0]["json"] == [
        {
            "itemType": "appointment",
            "delete": {"id": "s1", "version": 9},
            "selectedDay": "2019-02-05",
            "extent": "day",
        }
    ]


async def test_search_sends_the_term_encoded(stub):
    key = "0f8fad5b-d9cb-469f-a165-70867728950e-a"
    stub.route(
        "GET",
        SEARCH,
        json={"items": {key: {"highlight": {}, "item": {**STORED, "id": key}}}, "days": {}},
    )
    async with stub.client() as cozi:
        found, more = await cozi.search_calendar("a&b #c+d")
    assert ([a.id for a in found], more) == (["0f8fad5b-d9cb-469f-a165-70867728950e"], False)
    assert stub.sent("GET", SEARCH)[0]["query"]["q"] == "a&b #c+d"


async def test_print_view_is_bytes(stub):
    stub.route("GET", PRINT, body=b"%PDF-1.4")
    async with stub.client() as cozi:
        assert await cozi.get_calendar_print("week", date(2019, 1, 7)) == b"%PDF-1.4"


async def test_parse_event(stub):
    stub.route(
        "POST",
        PARSE,
        json={
            "parsedEvent": {
                "description": "Dentist",
                "day": "2019-01-08",
                "startTime": "15:00:00",
                "endTime": "16:00:00",
            }
        },
    )
    async with stub.client() as cozi:
        appt = await cozi.parse_event(
            "dentist tue 3pm", now=datetime(2019, 1, 7, 9, tzinfo=timezone.utc)
        )
    assert stub.sent("POST", PARSE)[0]["json"] == {
        "text": "dentist tue 3pm",
        "now": "2019-01-07T09:00:00+00:00",
    }
    assert (appt.subject, appt.start_day, appt.start_time) == ("Dentist", date(2019, 1, 8), time(15))
