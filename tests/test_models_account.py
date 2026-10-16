"""Account models and helpers, without HTTP (these also run on aiohttp>=3.14)."""

import re

import pytest
from pydantic import ValidationError

from cozi_client._transport import validate_id
from cozi_client.account import (
    FAMILY_FEED_ID,
    HOLIDAY_CALENDARS,
    AgendaChannel,
    CoziCalendarSubscription,
    CoziHousehold,
    CoziIcalFeed,
    CoziPerson,
    EmailStatus,
    NotificationChannel,
    PersonSettings,
    default_settings,
)

# A member as GET /account/person/ returns it (live key set, 2026-10-16).
WEB_PERSON = {
    "accountPersonId": "p1",
    "name": "Ann",
    "email": "ann@example.com",
    "emailStatus": "verified",
    "colorIndex": 5,
    "settings": {
        "calendarItemNotification": {
            "emailEnabled": True,
            "pushEnabled": False,
            "sendByDefault": True,
        },
        "calendarItemReminder": {"emailEnabled": False, "pushEnabled": True},
        "dailyAgenda": {"emailEnabled": False},
        "weeklyAgenda": {"emailEnabled": True},
    },
    "accountCreator": True,
    "accountPersonType": "adult",
    "isAdult": True,
    "notifiable": True,
    "notifiableFeatures": ["calendarItemNotification"],
    "phoneNumberKey": None,
    "version": 7,
    "futureKey": {"x": 1},
}


class TestPerson:
    def test_web_shape_parses_typed_settings(self):
        p = CoziPerson.model_validate(WEB_PERSON)
        assert (p.id, p.name, p.email, p.color, p.version) == ("p1", "Ann", "ann@example.com", 5, 7)
        s = p.settings
        assert (
            s.calendar_item_notification.email_enabled,
            s.calendar_item_notification.push_enabled,
        ) == (True, False)
        assert (s.calendar_item_reminder.email_enabled, s.calendar_item_reminder.push_enabled) == (
            False,
            True,
        )
        assert (s.daily_agenda.email_enabled, s.weekly_agenda.email_enabled) == (False, True)
        assert s.any_email is True

    def test_email_status_known_value_is_the_enum(self):
        p = CoziPerson.model_validate(WEB_PERSON)
        assert type(p.email_status) is EmailStatus
        assert p.email_status is EmailStatus.VERIFIED

    def test_email_status_live_value_for_a_member_without_email(self):
        p = CoziPerson.model_validate({"accountPersonId": "p2", "emailStatus": "not verified"})
        assert p.email_status is EmailStatus.NOT_VERIFIED

    def test_email_status_unknown_value_stays_a_str(self):
        p = CoziPerson.model_validate({"accountPersonId": "p2", "emailStatus": "frozen"})
        assert type(p.email_status) is str
        assert p.email_status == "frozen"

    def test_unknown_keys_survive_at_every_level(self):
        p = CoziPerson.model_validate(WEB_PERSON)
        assert p.model_extra == {"futureKey": {"x": 1}}
        assert p.settings.calendar_item_notification.model_extra == {"sendByDefault": True}
        assert p.settings.model_dump(by_alias=True) == WEB_PERSON["settings"]

    def test_populate_by_name_and_by_alias(self):
        by_name = CoziPerson(id="p3", name="Kid", color=2)
        by_alias = CoziPerson.model_validate(
            {"accountPersonId": "p3", "name": "Kid", "colorIndex": 2}
        )
        assert by_name == by_alias

    def test_missing_id_is_rejected(self):
        with pytest.raises(ValidationError):
            CoziPerson.model_validate({"name": "nobody"})

    def test_partial_settings_parse_with_channels_off(self):
        s = PersonSettings.model_validate({"dailyAgenda": {"emailEnabled": True}})
        assert s.model_dump(by_alias=True) == {
            "calendarItemNotification": {"emailEnabled": False, "pushEnabled": False},
            "calendarItemReminder": {"emailEnabled": False, "pushEnabled": False},
            "dailyAgenda": {"emailEnabled": True},
            "weeklyAgenda": {"emailEnabled": False},
        }

    def test_any_email_is_false_with_only_push(self):
        s = PersonSettings(
            calendar_item_notification=NotificationChannel(push_enabled=True),
            weekly_agenda=AgendaChannel(email_enabled=False),
        )
        assert s.any_email is False


class TestDefaultSettings:
    def test_with_email_matches_the_web(self):
        assert default_settings("a@example.com").model_dump(by_alias=True) == {
            "calendarItemNotification": {"emailEnabled": True, "pushEnabled": True},
            "calendarItemReminder": {"emailEnabled": True, "pushEnabled": True},
            "dailyAgenda": {"emailEnabled": False},
            "weeklyAgenda": {"emailEnabled": True},
        }

    def test_without_email_turns_every_email_channel_off(self):
        assert default_settings("").model_dump(by_alias=True) == {
            "calendarItemNotification": {"emailEnabled": False, "pushEnabled": True},
            "calendarItemReminder": {"emailEnabled": False, "pushEnabled": True},
            "dailyAgenda": {"emailEnabled": False},
            "weeklyAgenda": {"emailEnabled": False},
        }


class TestHousehold:
    def test_web_shape_with_extras(self):
        raw = {
            "accountId": "acct-1",
            "name": "The Smiths",
            "colorIndex": 3,
            "timezone": "America/New_York",
            "cobrand": "COZI",
            "creationDate": "2015-02-01",
            "analyticsOn": False,
            "photo": {"thumbUrl": "https://example/t.jpg", "isDefault": True, "width": 10},
            "languageDesignator": "en",
        }
        h = CoziHousehold.model_validate(raw)
        assert (h.account_id, h.name, h.color_index, h.timezone, h.cobrand) == (
            "acct-1",
            "The Smiths",
            3,
            "America/New_York",
            "COZI",
        )
        assert (h.creation_date, h.analytics_on) == ("2015-02-01", False)
        assert h.photo == raw["photo"]
        assert h.model_extra == {"languageDesignator": "en"}
        assert h.model_dump(by_alias=True, exclude_unset=True) == raw


class TestFeedsAndCalendars:
    def test_feed_url_is_not_in_repr(self):
        feed = CoziIcalFeed.model_validate({"status": "enabled", "url": "webcal://secret-token"})
        assert feed.url == "webcal://secret-token"
        assert "secret-token" not in repr(feed)
        assert "secret-token" not in str(feed)

    def test_subscription_parses_and_hides_the_source_url(self):
        sub = CoziCalendarSubscription.model_validate(
            {
                "calendar": {
                    "id": HOLIDAY_CALENDARS["US"],
                    "name": "US Holidays",
                    "url": "https://private/ics",
                },
                "attendees": ["p1"],
            }
        )
        assert (sub.calendar.id, sub.calendar.name, sub.attendees) == (
            HOLIDAY_CALENDARS["US"],
            "US Holidays",
            ["p1"],
        )
        assert "private" not in repr(sub)

    def test_holiday_ids_and_family_feed_id_are_valid_ids(self):
        assert sorted(HOLIDAY_CALENDARS) == ["AU", "CA", "NEWS", "UK", "US"]
        assert all(re.fullmatch(r"[0-9a-f]{40}", v) for v in HOLIDAY_CALENDARS.values())
        assert validate_id(FAMILY_FEED_ID) == "00000000-0000-0000-0000-000000000000"
