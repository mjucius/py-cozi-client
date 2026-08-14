"""Tests for utils.py."""

from datetime import date, time

import pytest

from exceptions import ValidationError
from utils import (
    ID_PATTERN,
    calculate_date_span,
    extract_attendee_names,
    format_time_for_display,
    get_week_boundaries,
    is_weekend,
    parse_time_string,
    sanitize_item_text,
    sanitize_list_title,
    split_long_text,
    validate_calendar_period,
    validate_email,
    validate_id,
)


class TestValidateEmail:
    @pytest.mark.parametrize(
        "email",
        [
            "user@example.com",
            "first.last@example.co.uk",
            "user+tag@example.com",
            "u_s_e_r@sub.example.com",
            "123@example.io",
        ],
    )
    def test_valid(self, email):
        assert validate_email(email) is True

    @pytest.mark.parametrize(
        "email",
        [
            "",
            "no-at-sign",
            "missing@tld",
            "@nouser.com",
            "user@.com",
            "user@example",
            "user @example.com",
        ],
    )
    def test_invalid(self, email):
        assert validate_email(email) is False


class TestParseTimeString:
    def test_24h(self):
        assert parse_time_string("14:30") == time(14, 30)

    def test_24h_zero_padded(self):
        assert parse_time_string("09:05") == time(9, 5)

    def test_12h_pm(self):
        assert parse_time_string("2:30 PM") == time(14, 30)

    def test_12h_am(self):
        assert parse_time_string("2:30 AM") == time(2, 30)

    def test_12h_no_space(self):
        assert parse_time_string("2:30PM") == time(14, 30)

    def test_12h_noon(self):
        assert parse_time_string("12:00 PM") == time(12, 0)

    def test_12h_midnight(self):
        assert parse_time_string("12:00 AM") == time(0, 0)

    def test_12h_hour_only(self):
        assert parse_time_string("3 PM") == time(15, 0)

    def test_lowercase_meridiem(self):
        assert parse_time_string("2:30 pm") == time(14, 30)

    def test_invalid_returns_none(self):
        assert parse_time_string("not a time") is None

    def test_out_of_range_returns_none(self):
        assert parse_time_string("25:00") is None
        assert parse_time_string("12:99") is None

    def test_empty_returns_none(self):
        assert parse_time_string("") is None


class TestFormatTimeForDisplay:
    def test_12h_default(self):
        assert format_time_for_display(time(14, 30)) == "2:30 PM"

    def test_12h_morning(self):
        assert format_time_for_display(time(9, 5)) == "9:05 AM"

    def test_24h(self):
        assert format_time_for_display(time(14, 30), use_12_hour=False) == "14:30"

    def test_none(self):
        assert format_time_for_display(None) == ""


class TestCalculateDateSpan:
    def test_same_day(self):
        d = date(2026, 5, 1)
        assert calculate_date_span(d, d) == 1

    def test_consecutive_days(self):
        assert calculate_date_span(date(2026, 5, 1), date(2026, 5, 2)) == 2

    def test_week(self):
        assert calculate_date_span(date(2026, 5, 1), date(2026, 5, 7)) == 7

    def test_end_before_start_clamps_to_one(self):
        assert calculate_date_span(date(2026, 5, 5), date(2026, 5, 1)) == 1


class TestSplitLongText:
    def test_short_passthrough(self):
        assert split_long_text("hello", max_length=100) == ["hello"]

    def test_splits_on_word_boundary(self):
        text = "one two three four five six seven"
        chunks = split_long_text(text, max_length=10)
        for chunk in chunks:
            assert len(chunk) <= 10
        assert " ".join(chunks) == text

    def test_custom_max_length(self):
        chunks = split_long_text("aaaa bbbb cccc dddd", max_length=4)
        assert len(chunks) == 4


class TestSanitizers:
    def test_list_title_strips(self):
        assert sanitize_list_title("  groceries  ") == "groceries"

    def test_list_title_truncates_at_255(self):
        result = sanitize_list_title("x" * 300)
        assert len(result) == 255

    def test_item_text_strips(self):
        assert sanitize_item_text("  milk  ") == "milk"

    def test_item_text_truncates_at_1000(self):
        result = sanitize_item_text("x" * 1500)
        assert len(result) == 1000


class TestExtractAttendeeNames:
    def test_partition(self):
        known, unknown = extract_attendee_names(
            ["Alice", "Bob", "Carol"], ["Alice", "Bob"]
        )
        assert known == ["Alice", "Bob"]
        assert unknown == ["Carol"]

    def test_empty_inputs(self):
        assert extract_attendee_names([], []) == ([], [])

    def test_case_sensitive(self):
        known, unknown = extract_attendee_names(["alice"], ["Alice"])
        assert known == []
        assert unknown == ["alice"]


class TestIsWeekend:
    @pytest.mark.parametrize(
        "d,expected",
        [
            (date(2026, 5, 4), False),  # Monday
            (date(2026, 5, 5), False),  # Tuesday
            (date(2026, 5, 6), False),  # Wednesday
            (date(2026, 5, 7), False),  # Thursday
            (date(2026, 5, 8), False),  # Friday
            (date(2026, 5, 9), True),  # Saturday
            (date(2026, 5, 10), True),  # Sunday
        ],
    )
    def test_weekday_coverage(self, d, expected):
        assert is_weekend(d) is expected


class TestGetWeekBoundaries:
    """Regression test for the timedelta import bug."""

    def test_midweek(self):
        # Sat 2026-05-02 → week starts Mon 2026-04-27, ends Sun 2026-05-03
        start, end = get_week_boundaries(date(2026, 5, 2))
        assert start == date(2026, 4, 27)
        assert end == date(2026, 5, 3)

    def test_monday(self):
        start, end = get_week_boundaries(date(2026, 5, 4))
        assert start == date(2026, 5, 4)
        assert end == date(2026, 5, 10)

    def test_sunday(self):
        start, end = get_week_boundaries(date(2026, 5, 10))
        assert start == date(2026, 5, 4)
        assert end == date(2026, 5, 10)


# VULN-001 / VULN-002 (CWE-22). Ids are interpolated into request paths and into
# JSON-Pointer paths, so the character class is the whole defense -- see the
# comment on ID_PATTERN in utils.py. Mirrors cozi_mcp's
# tests/security-path-traversal.test.ts.
class TestValidateId:
    @pytest.mark.parametrize(
        "bad",
        [
            "../../evil",  # escapes the path prefix
            "a/b",  # extra path segment, and retargets a JSON-Pointer
            "..",
            "x?y",  # truncates the path into a query string
            "a#b",  # truncates the path into a fragment
            "a%2e",  # percent-encoded traversal
            "a~b",  # JSON-Pointer escape character
            "a b",
            "",
        ],
    )
    def test_rejects(self, bad):
        with pytest.raises(ValidationError):
            validate_id(bad)

    @pytest.mark.parametrize("good", ["AbC_123-def", "l1", "list-GUID_1", "0"])
    def test_accepts_and_returns_unchanged(self, good):
        assert ID_PATTERN.match(good)
        assert validate_id(good) == good

    def test_rejects_non_string(self):
        with pytest.raises(ValidationError):
            validate_id(None)

    def test_message_names_the_parameter(self):
        with pytest.raises(ValidationError, match="Invalid list_id"):
            validate_id("../evil", "list_id")

    def test_newline_cannot_smuggle_a_second_segment(self):
        # re.match with `$` would accept "good\n"; ID_PATTERN must not.
        with pytest.raises(ValidationError):
            validate_id("good\n../evil")


class TestValidateCalendarPeriod:
    def test_accepts_valid_period(self):
        assert validate_calendar_period(2026, 5) == (2026, 5)

    @pytest.mark.parametrize(
        "year, month",
        [
            ("../../../evil", 5),  # the traversal the int annotation does not stop
            (2026, "13/../.."),
            (2026, 0),
            (2026, 13),
            (1899, 5),
            (3000, 5),
            (None, 5),
            (2026.0, 5),
        ],
    )
    def test_rejects(self, year, month):
        with pytest.raises(ValidationError):
            validate_calendar_period(year, month)

    def test_rejects_bool_as_month(self):
        # bool subclasses int, so True would otherwise sail through as month 1.
        with pytest.raises(ValidationError):
            validate_calendar_period(2026, True)
