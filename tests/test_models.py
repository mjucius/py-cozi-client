"""Tests for Pydantic models in models.py."""

from datetime import date, datetime, time

import pytest

from models import (
    CoziAppointment,
    CoziItem,
    CoziList,
    CoziPerson,
    ItemStatus,
    ListType,
)


class TestCoziPerson:
    def test_alias_resolution(self):
        p = CoziPerson(
            accountPersonId="p1",
            name="Alice",
            phoneNumberKey="555-1234",
        )
        assert p.id == "p1"
        assert p.phone == "555-1234"

    def test_no_duplicate_phone_field(self):
        """After the duplicate-alias fix, phone_number_key no longer exists."""
        assert "phone_number_key" not in CoziPerson.model_fields

    def test_optional_fields_default_none(self):
        p = CoziPerson(accountPersonId="p1")
        assert p.email is None
        assert p.phone is None


class TestCoziItem:
    def test_status_string_to_enum(self):
        item = CoziItem(itemId="i1", text="Milk", status="complete")
        assert item.status == "complete"  # use_enum_values = True

    def test_due_date_iso_string(self):
        item = CoziItem(itemId="i1", text="x", dueDate="2026-05-02")
        assert item.due_date == date(2026, 5, 2)

    def test_due_date_invalid_returns_none(self):
        item = CoziItem(itemId="i1", text="x", dueDate="not-a-date")
        assert item.due_date is None

    def test_datetime_fields_with_z_suffix(self):
        item = CoziItem(itemId="i1", text="x", createdAt="2026-05-02T10:00:00Z")
        assert item.created_at is not None
        assert item.created_at.year == 2026

    def test_datetime_invalid_returns_none(self):
        item = CoziItem(itemId="i1", text="x", createdAt="garbage")
        assert item.created_at is None

    def test_id_field_root_validator_falls_back_from_id(self):
        item = CoziItem(id="legacy-id", text="x")
        assert item.id == "legacy-id"


class TestCoziList:
    def test_list_type_string_to_enum(self):
        lst = CoziList(listId="l1", title="Groceries", listType="shopping")
        assert lst.list_type == "shopping"

    def test_items_constructed_from_dicts(self):
        lst = CoziList(
            listId="l1",
            title="x",
            listType="todo",
            items=[
                {"itemId": "i1", "text": "a", "status": "incomplete"},
                {"itemId": "i2", "text": "b", "status": "complete"},
            ],
        )
        assert len(lst.items) == 2
        assert all(isinstance(i, CoziItem) for i in lst.items)
        assert lst.items[1].text == "b"

    def test_id_field_root_validator(self):
        lst = CoziList(id="legacy", title="x", listType="todo")
        assert lst.id == "legacy"

    def test_empty_items_default(self):
        lst = CoziList(listId="l1", title="x", listType="todo")
        assert lst.items == []


class TestCoziAppointment:
    def _base(self, **overrides):
        data = {
            "id": "a1",
            "description": "Soccer practice",
            "day": "2026-05-02",
            "startTime": "14:30:00",
            "endTime": "15:30:00",
        }
        data.update(overrides)
        return data

    def test_basic_construction(self):
        appt = CoziAppointment.model_validate(self._base())
        assert appt.id == "a1"
        assert appt.subject == "Soccer practice"
        assert appt.start_day == date(2026, 5, 2)
        assert appt.start_time == time(14, 30)
        assert appt.end_time == time(15, 30)

    def test_invalid_date_falls_back_to_today(self):
        appt = CoziAppointment.model_validate(self._base(day="garbage"))
        assert appt.start_day == date.today()

    def test_invalid_time_returns_none(self):
        appt = CoziAppointment.model_validate(self._base(startTime="bad"))
        assert appt.start_time is None

    def test_extract_item_details_flattens(self):
        data = self._base()
        data["itemDetails"] = {"location": "Field A", "notes": "bring water"}
        appt = CoziAppointment.model_validate(data)
        assert appt.location == "Field A"
        assert appt.notes == "bring water"

    def test_description_short_fallback(self):
        data = self._base()
        del data["description"]
        data["descriptionShort"] = "Practice"
        appt = CoziAppointment.model_validate(data)
        assert appt.subject == "Practice"

    def test_start_date_property(self):
        appt = CoziAppointment.model_validate(self._base())
        assert appt.start_date == appt.start_day

    def test_to_api_create_format(self):
        appt = CoziAppointment.model_validate(
            self._base(
                location="Field A",
                householdMembers=["Alice", "Bob"],
            )
        )
        out = appt.to_api_create_format()
        assert out["itemType"] == "appointment"
        details = out["create"]["details"]
        assert details["subject"] == "Soccer practice"
        assert details["startTime"] == "14:30"
        assert details["endTime"] == "15:30"
        assert details["location"] == "Field A"
        assert details["attendeeSet"] == ["Alice", "Bob"]
        assert out["create"]["startDay"] == "2026-05-02"

    def test_to_api_edit_format(self):
        appt = CoziAppointment.model_validate(self._base())
        out = appt.to_api_edit_format()
        assert out["edit"]["id"] == "a1"
        assert out["edit"]["details"]["subject"] == "Soccer practice"

    def test_to_api_edit_requires_id(self):
        data = self._base()
        del data["id"]
        appt = CoziAppointment.model_validate(data)
        with pytest.raises(ValueError, match="without ID"):
            appt.to_api_edit_format()

    def test_to_api_delete_format(self):
        appt = CoziAppointment.model_validate(self._base())
        out = appt.to_api_delete_format()
        assert out == {"itemType": "appointment", "delete": {"id": "a1"}}

    def test_to_api_delete_requires_id(self):
        data = self._base()
        del data["id"]
        appt = CoziAppointment.model_validate(data)
        with pytest.raises(ValueError, match="without ID"):
            appt.to_api_delete_format()

    def test_no_times_serializes_as_null(self):
        data = self._base()
        del data["startTime"]
        del data["endTime"]
        appt = CoziAppointment.model_validate(data)
        out = appt.to_api_create_format()
        assert out["create"]["details"]["startTime"] is None
        assert out["create"]["details"]["endTime"] is None

class TestRecurrencePreservation:
    """
    Cozi's calendar edit is a full replace, so a recurrence rule that isn't
    re-sent is erased and the series collapses to a single appointment.
    Wire shape verified against the live Cozi web client (see
    CoziAppointment.to_api_edit_format).
    """

    RULE = {
        "rules": [
            {"frequency": "Weekly", "interval": 1, "byDay": ["FR"], "end": {}}
        ],
        "endDay": "2026-12-31",
    }

    def _recurring(self, **overrides):
        data = {
            "id": "a1",
            "description": "Soccer practice",
            "day": "2026-05-02",
            "startTime": "14:30:00",
            "endTime": "15:30:00",
            "itemDetails": {
                "recurrence": self.RULE,
                "recurrenceStartDay": "2026-01-02",
                "notes": "bring cleats",
            },
        }
        data.update(overrides)
        return CoziAppointment.model_validate(data)

    def test_recurrence_parsed_from_item_details(self):
        # A GET nests recurrence under itemDetails; it must be hoisted.
        appt = self._recurring()
        assert appt.recurrence == self.RULE
        assert appt.recurrence_start_day == "2026-01-02"

    def test_edit_preserves_recurrence(self):
        out = self._recurring().to_api_edit_format()
        assert out["edit"]["recurrence"] == self.RULE

    def test_recurrence_is_sibling_of_details_not_nested(self):
        # The asymmetry that breaks series: an edit wants recurrence at the edit
        # level. Nested inside `details` it is ignored and the rule is unset.
        out = self._recurring().to_api_edit_format()
        assert "recurrence" in out["edit"]
        assert "recurrence" not in out["edit"]["details"]

    def test_edit_never_sends_item_version(self):
        # Including itemVersion makes Cozi silently discard the whole edit.
        appt = self._recurring(itemVersion=7)
        assert appt.item_version == 7
        out = appt.to_api_edit_format()
        assert "itemVersion" not in out["edit"]
        assert "itemVersion" not in out["edit"]["details"]

    def test_end_day_rides_inside_recurrence(self):
        # endDay is not a top-level field; it round-trips within recurrence.
        out = self._recurring().to_api_edit_format()
        assert out["edit"]["recurrence"]["endDay"] == "2026-12-31"

    def test_edit_omits_recurrence_for_one_off(self):
        appt = CoziAppointment.model_validate(
            {"id": "a1", "description": "Dentist", "day": "2026-05-02"}
        )
        out = appt.to_api_edit_format()
        assert "recurrence" not in out["edit"]

    def test_unrelated_edit_keeps_the_series(self):
        # The regression this guards: changing notes must not strip the schedule.
        appt = self._recurring()
        appt.notes = "bring cleats and water"
        out = appt.to_api_edit_format()
        assert out["edit"]["details"]["notes"] == "bring cleats and water"
        assert out["edit"]["recurrence"] == self.RULE
