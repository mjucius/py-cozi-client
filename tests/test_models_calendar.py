"""CoziAppointment, the calendar helpers and the payload guards (pure; no HTTP)."""

import copy
from datetime import date, time

import pytest
from pydantic import ValidationError

from cozi_client.calendar import (
    MEAL_SLOTS,
    WHOLE_SERIES,
    CoziAppointment,
    Extent,
    Occurrence,
    _details,
    _occurrence_index,
    _parse_page,
    date_span_for_last_day,
    last_day_of,
    repeat_rule,
)
from cozi_client.exceptions import CoziValidationError

# A real-shaped month-page item (live 2026-10-16: itemDetails carries the id again).
SERIES = {
    "id": "s1",
    "description": "LIVE_TEST_DELETE_ME:calendar gym",
    "descriptionShort": "LIVE_TEST_DELETE_ME calendar gym",
    "day": "2019-01-08",
    "startTime": "07:00:00",
    "endTime": "07:30:00",
    "itemType": "appointment",
    "itemVersion": 4903,
    "reminders": [{"minutesBefore": 1440}, {"minutesBefore": 30}],
    "householdMembers": ["p1"],
    "itemDetails": {
        "id": "s1",
        "notes": "bring towel",
        "location": "Gym",
        "notifyAttendees": True,
        "recurrenceStartDay": "2019-01-08",
        "recurrence": {
            "endDay": "2019-02-12",
            "rules": [
                {
                    "frequency": "Weekly",
                    "interval": 1,
                    "end": {"untilDay": "2019-02-12"},
                    "byDay": ["TU"],
                }
            ],
            "text": ["Every Tuesday", " until Feb 12, 2019"],
            "exdates": [
                {"date": "2019-01-22T07:00:00"},
                {"date": "2019-01-29T07:00:00", "standinApptId": "x2"},
                "2019-01-15T07:00:00",
            ],
        },
    },
}


class TestWireParse:
    def test_hoists_details_without_touching_the_input(self):
        raw = copy.deepcopy(SERIES)
        a = CoziAppointment.model_validate(raw)
        assert raw == SERIES
        assert (a.id, a.subject, a.start_day) == (
            "s1",
            "LIVE_TEST_DELETE_ME:calendar gym",
            date(2019, 1, 8),
        )
        assert (a.start_time, a.end_time, a.date_span) == (time(7, 0), time(7, 30), 0)
        assert (a.notes, a.location, a.attendees) == ("bring towel", "Gym", ["p1"])
        assert (a.item_type, a.item_source, a.item_version) == ("appointment", None, 4903)
        assert a.reminders == [1440, 30]  # Cozi's order, kept
        assert a.recurrence_start_day == date(2019, 1, 8)
        assert a.recurrence == SERIES["itemDetails"]["recurrence"]
        assert a.__pydantic_extra__ == {
            "descriptionShort": "LIVE_TEST_DELETE_ME calendar gym",
            "notifyAttendees": True,
        }

    def test_item_id_wins_over_a_meal_details_id(self):
        a = CoziAppointment.model_validate({"day": "2019-01-08", "itemDetails": {"id": "meal-details"}})
        assert a.id is None

    def test_subject_defaults_and_falls_back_to_short_description(self):
        meal = CoziAppointment.model_validate({"id": "m1", "day": "2019-01-08", "itemType": "meal"})
        assert meal.subject == ""
        short = CoziAppointment.model_validate(
            {"day": "2019-01-08", "description": " ", "descriptionShort": " x "}
        )
        assert short.subject == "x"
        null = CoziAppointment.model_validate({"day": "2019-01-08", "description": None})
        assert null.subject == ""

    def test_both_midnight_is_all_day(self):
        a = CoziAppointment.model_validate(
            {"day": "2019-01-31", "startTime": "00:00:00", "endTime": "00:00:00", "dateSpan": 1}
        )
        assert (a.start_time, a.end_time, a.is_all_day, a.last_day) == (
            None,
            None,
            True,
            date(2019, 1, 31),
        )

    def test_midnight_start_alone_is_kept(self):
        a = CoziAppointment.model_validate(
            {"day": "2019-01-31", "startTime": "00:00:00", "endTime": "01:00:00"}
        )
        assert (a.start_time, a.end_time, a.is_all_day) == (time(0, 0), time(1, 0), False)

    def test_single_digit_hour_parses(self):
        a = CoziAppointment.model_validate(
            {"day": "2019-01-31", "startTime": "8:00:00", "endTime": "10:00:00"}
        )
        assert (a.start_time, a.end_time) == (time(8, 0), time(10, 0))

    @pytest.mark.parametrize("day", ["garbage", "2019-13-01", None])
    def test_bad_day_raises_instead_of_becoming_today(self, day):
        with pytest.raises(ValidationError):
            CoziAppointment.model_validate({"day": day, "description": "x"})

    def test_missing_day_raises(self):
        with pytest.raises(ValidationError):
            CoziAppointment.model_validate({"description": "x"})

    def test_bad_time_raises(self):
        with pytest.raises(ValidationError):
            CoziAppointment.model_validate(
                {"day": "2019-01-08", "startTime": "25:00:00", "endTime": "26:00:00"}
            )

    def test_reminders_absent_is_none(self):
        assert CoziAppointment.model_validate({"day": "2019-01-08"}).reminders is None

    def test_birthday_and_meal_fields(self):
        b = CoziAppointment.model_validate(
            {
                "day": "2019-02-14",
                "itemType": "birthday",
                "itemDetails": {"name": "Ann", "birthYear": 1990, "householdMember": "p1"},
            }
        )
        assert (b.name, b.birth_year, b.__pydantic_extra__) == ("Ann", 1990, {"householdMember": "p1"})
        m = CoziAppointment.model_validate(
            {
                "day": "2019-02-14",
                "itemType": "meal",
                "itemDetails": {"mealSlot": "dinner", "mealItems": [{"text": "Soup"}]},
            }
        )
        assert (m.meal_slot, m.meal_items, m.is_editable) == ("dinner", [{"text": "Soup"}], False)


class TestAssignment:
    def test_assignment_neither_rehoists_nor_leaks_extras(self):
        a = CoziAppointment.model_validate(SERIES)
        extras = dict(a.__pydantic_extra__)
        a.notes = None
        a.subject = "new"
        assert (a.notes, a.subject) == (None, "new")
        assert a.__pydantic_extra__ == extras

    def test_assignment_is_validated(self):
        a = CoziAppointment.model_validate(SERIES)
        a.start_time = "9:30"
        assert a.start_time == time(9, 30)
        with pytest.raises(ValidationError):
            a.start_day = "not a day"

    def test_construct_by_field_name(self):
        a = CoziAppointment(
            start_day=date(2019, 1, 8), subject="x", start_time=time(9), end_time=time(10)
        )
        assert (a.subject, a.item_type, a.occurrence_days, a.is_all_day) == ("x", None, [], False)


class TestProperties:
    def test_series(self):
        a = CoziAppointment.model_validate(SERIES)
        assert a.is_recurring is True
        assert a.recurrence_text == "Every Tuesday until Feb 12, 2019"
        assert a.end_day == date(2019, 2, 12)
        assert a.skipped_days == [date(2019, 1, 15), date(2019, 1, 22)]
        assert a.is_editable is True

    @pytest.mark.parametrize(
        "recurrence",
        [
            {"rules": [{"frequency": "Daily"}]},
            {"text": ["Every day"]},
            {"text": "Every day"},
            {"endDay": "2019-02-01"},
        ],
    )
    def test_any_recurrence_marker_is_recurring(self, recurrence):
        assert CoziAppointment(start_day=date(2019, 1, 1), recurrence=recurrence).is_recurring is True

    def test_one_off(self):
        a = CoziAppointment(start_day=date(2019, 1, 1))
        assert (a.is_recurring, a.recurrence_text, a.end_day, a.skipped_days) == (False, None, None, [])

    @pytest.mark.parametrize(
        "extra",
        [{"itemSource": "US Holidays"}, {"itemDetails": {"readOnly": True}}, {"itemType": "meal"}],
    )
    def test_not_editable(self, extra):
        assert CoziAppointment.model_validate({"day": "2019-01-01", **extra}).is_editable is False

    def test_last_day(self):
        timed = CoziAppointment(
            start_day=date(2019, 1, 30), start_time=time(10), end_time=time(11), date_span=2
        )
        all_day = CoziAppointment(start_day=date(2019, 1, 30), date_span=3)
        assert (timed.last_day, all_day.last_day) == (date(2019, 2, 1), date(2019, 2, 1))


class TestDayMap:
    def test_occurrence_index_uses_multiday_start(self):
        days = {
            "2019-01-08": [{"id": "s1"}],
            "2019-01-15": [{"id": "s1"}],
            "2019-01-29": [{"id": "s1"}],
            "2019-01-30": [{"id": "t1", "multiday": {"startDay": "2019-01-30", "endDay": "2019-02-01"}}],
            "2019-01-31": [{"id": "t1", "multiday": {"startDay": "2019-01-30", "endDay": "2019-02-01"}}],
            "2019-02-01": [{"id": "p1", "multiday": {"startDay": "2019-01-31", "endDay": "2019-02-02"}}],
            "junk": [{"id": "s1"}],
            "2019-01-09": "junk",
        }
        assert _occurrence_index(days) == {
            "s1": [date(2019, 1, 8), date(2019, 1, 15), date(2019, 1, 29)],
            "t1": [date(2019, 1, 30)],
            "p1": [date(2019, 1, 31)],
        }

    def test_page_falls_back_to_the_key_for_id(self):
        page = _parse_page(
            {
                "items": {"k1": {"day": "2019-01-08", "description": "x"}},
                "days": {"2019-01-08": [{"id": "k1"}]},
            }
        )
        assert (page["k1"].id, page["k1"].occurrence_days) == ("k1", [date(2019, 1, 8)])


class TestHelpers:
    def test_span_helpers(self):
        d = date(2019, 1, 31)
        assert date_span_for_last_day(d, d, False) == 0
        assert date_span_for_last_day(d, date(2019, 2, 1), False) == 1
        assert date_span_for_last_day(d, d, True) == 1
        assert date_span_for_last_day(date(2026, 7, 30), date(2026, 8, 2), True) == 4
        assert last_day_of(date(2026, 7, 30), 4, True) == date(2026, 8, 2)
        assert last_day_of(d, 0, True) == d
        assert last_day_of(d, 1, False) == date(2019, 2, 1)
        with pytest.raises(CoziValidationError):
            date_span_for_last_day(d, date(2019, 1, 30), True)

    def test_scope_values(self):
        assert (Extent.DAY.value, Extent.FORWARD.value, repr(WHOLE_SERIES)) == (
            "day",
            "forward",
            "WHOLE_SERIES",
        )
        assert Occurrence(date(2019, 1, 15), Extent.DAY) == Occurrence(date(2019, 1, 15), Extent.DAY)

    def test_meal_slot_times_are_the_webs(self):
        assert MEAL_SLOTS == {
            "breakfast": ("8:00:00", "10:00:00"),
            "lunch": ("12:00:00", "14:00:00"),
            "dinner": ("18:00:00", "20:00:00"),
            "snack": ("21:00:00", "23:00:00"),
        }


class TestRepeatRule:
    # The rule shapes cozi_mcp verified live (tests/live/calendar.test.ts).
    def test_weekly_days(self):
        assert repeat_rule(
            "weekly", start_day=date(2019, 1, 8), weekdays=["th", "TU"], until=date(2019, 1, 24)
        ) == {
            "rules": [
                {
                    "frequency": "Weekly",
                    "interval": 1,
                    "byDay": ["TU", "TH"],
                    "end": {"untilDay": "2019-01-24"},
                }
            ]
        }

    def test_weekly_defaults_to_the_start_weekday_open_ended(self):
        assert repeat_rule("Weekly", start_day=date(2019, 1, 6)) == {
            "rules": [{"frequency": "Weekly", "interval": 1, "byDay": ["SU"], "end": {}}]
        }

    def test_monthly_fifth_weekday_is_last(self):
        assert repeat_rule(
            "monthly", start_day=date(2019, 1, 29), monthly_by="weekday", until=date(2019, 3, 31)
        ) == {
            "rules": [
                {
                    "frequency": "Monthly",
                    "interval": 1,
                    "byDay": ["-1TU"],
                    "end": {"untilDay": "2019-03-31"},
                }
            ]
        }

    def test_monthly_second_weekday_and_by_date(self):
        assert repeat_rule("monthly", start_day=date(2019, 1, 8), monthly_by="weekday")["rules"][0][
            "byDay"
        ] == ["2TU"]
        assert repeat_rule("monthly", start_day=date(2019, 1, 8))["rules"][0]["byMonthDay"] == [8]

    def test_daily_interval(self):
        assert repeat_rule("daily", start_day=date(2019, 2, 1), interval=2, until=date(2019, 2, 7)) == {
            "rules": [{"frequency": "Daily", "interval": 2, "end": {"untilDay": "2019-02-07"}}]
        }

    def test_yearly(self):
        assert repeat_rule("yearly", start_day=date(2019, 3, 20), until=date(2019, 3, 31)) == {
            "rules": [
                {
                    "frequency": "Yearly",
                    "interval": 1,
                    "byMonth": [3],
                    "byMonthDay": [20],
                    "end": {"untilDay": "2019-03-31"},
                }
            ]
        }

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"frequency": "hourly"},
            {"frequency": "weekly", "weekdays": ["WE"]},  # 2019-01-08 is a Tuesday
            {"frequency": "weekly", "weekdays": ["TU", "XX"]},
            {"frequency": "daily", "weekdays": ["TU"]},
            {"frequency": "daily", "monthly_by": "weekday"},
            {"frequency": "monthly", "monthly_by": "nth"},
            {"frequency": "daily", "interval": 0},
            {"frequency": "daily", "interval": True},
            {"frequency": "daily", "until": date(2019, 1, 7)},
        ],
    )
    def test_refusals(self, kwargs):
        with pytest.raises(CoziValidationError):
            repeat_rule(start_day=date(2019, 1, 8), **kwargs)


def _appt(**kw):
    return CoziAppointment(start_day=date(2019, 1, 8), subject="x", **kw)


class TestDetailsGuards:
    def test_all_day_is_midnight_and_at_least_a_day(self):
        assert _details(_appt()) == {
            "startTime": "00:00",
            "endTime": "00:00",
            "dateSpan": 1,
            "attendeeSet": [],
            "location": None,
            "notes": None,
        }
        assert _details(_appt(date_span=3))["dateSpan"] == 3

    def test_timed_span_goes_out_untouched(self):
        d = _details(_appt(start_time=time(0, 0), end_time=time(1, 0)))
        assert (d["startTime"], d["endTime"], d["dateSpan"]) == ("00:00", "01:00", 0)
        assert _details(_appt(start_time=time(20), end_time=time(2), date_span=1))["dateSpan"] == 1

    @pytest.mark.parametrize(
        "kw",
        [
            {"start_time": time(9)},
            {"end_time": time(9)},
            {"start_time": time(10), "end_time": time(9)},
            {"date_span": -1},
        ],
    )
    def test_refused(self, kw):
        with pytest.raises(CoziValidationError):
            _details(_appt(**kw))

    def test_self_overlapping_series_refused(self):
        daily = repeat_rule("daily", start_day=date(2019, 1, 8))
        # 25 h, repeating daily: the web refuses it.
        with pytest.raises(CoziValidationError, match="overlap"):
            _details(_appt(start_time=time(9), end_time=time(10), date_span=1, recurrence=daily))
        # Two all-day days, daily.
        with pytest.raises(CoziValidationError, match="overlap"):
            _details(_appt(date_span=2, recurrence=daily))

    def test_series_that_fit_their_period(self):
        daily = repeat_rule("daily", start_day=date(2019, 1, 8))
        _details(
            _appt(start_time=time(9), end_time=time(10), recurrence=daily)
        )  # same day: never checked
        _details(_appt(date_span=1, recurrence=daily))  # one all-day day, daily: length == period
        _details(
            _appt(start_time=time(20), end_time=time(2), date_span=1, recurrence=daily)
        )  # overnight
        weekly = repeat_rule("weekly", start_day=date(2019, 1, 8), interval=2)
        _details(_appt(date_span=8, recurrence=weekly))
