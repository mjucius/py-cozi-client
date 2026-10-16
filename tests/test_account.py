"""Account endpoints with aioresponses: exact method, path and body for each, plus the guards."""

import aiohttp
import pytest

from cozi_client.account import (
    FAMILY_FEED_ID,
    HOLIDAY_CALENDARS,
    CoziPerson,
    NotificationChannel,
    PersonSettings,
    default_settings,
)
from cozi_client.exceptions import CoziValidationError, ResourceNotFoundError, ResponseParseError
from tests.conftest import REAUTH_PATH, TEST_ACCESS_TOKEN, api, sent, sent_query

UNVERSIONED = "https://rest.cozi.com/api"
PERSONS = "/2004/{acct}/account/person/"
HOUSEHOLD = "/2004/{acct}/account/"
CONFIG = "/2004/{acct}/config/"
UPLOAD = "/2111/{acct}/photo/uploader.json"
SHARED = "/calendar/sharedcalendar/{acct}/"
US = HOLIDAY_CALENDARS["US"]

OFF = {
    "calendarItemNotification": {"emailEnabled": False, "pushEnabled": False},
    "calendarItemReminder": {"emailEnabled": False, "pushEnabled": False},
    "dailyAgenda": {"emailEnabled": False},
    "weeklyAgenda": {"emailEnabled": False},
}


def person(pid, email="", color=0, **extra):
    return {
        "accountPersonId": pid,
        "name": f"name-{pid}",
        "email": email or None,
        "colorIndex": color,
        "emailStatus": "verified" if email else "not verified",
        "settings": OFF,
        "version": 3,
        **extra,
    }


def household(**changes):
    return {
        "accountId": "acct-123",
        "name": "Home",
        "colorIndex": 1,
        "timezone": "America/New_York",
        **changes,
    }


def count(mocked):
    """Every request sent so far (mocked.requests groups them by URL)."""
    return sum(len(calls) for calls in mocked.requests.values())


def bodies(mocked, method, path, root="https://rest.cozi.com/api/ext"):
    return [kw.get("json") for kw in sent(mocked, method, path, root=root)]


class TestPersons:
    async def test_get_family_members(self, client, mocked):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com", 4)])
        members = await client.get_family_members()
        assert [(m.id, m.email, m.color) for m in members] == [("p1", "a@x.com", 4)]
        assert len(sent(mocked, "GET", PERSONS)) == 1

    async def test_get_family_members_non_list_is_parse_error(self, client, mocked):
        mocked.get(api(PERSONS), payload={"error": "nope"})
        with pytest.raises(ResponseParseError):
            await client.get_family_members()

    async def test_create_with_colour_and_settings_posts_once(self, client, mocked):
        mocked.post(api(PERSONS), status=201, payload=person("p9", color=6))
        created = await client.create_person(
            "Kid", color_index=6, settings=PersonSettings.model_validate(OFF)
        )
        assert bodies(mocked, "POST", PERSONS) == [
            {"name": "Kid", "email": "", "colorIndex": 6, "settings": OFF}
        ]
        assert sent(mocked, "GET", PERSONS) == []
        assert (created.id, created.color) == ("p9", 6)

    async def test_create_defaults_first_unused_colour_and_web_settings(
        self, client, mocked, no_sleep
    ):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com", 0), person("p2", "", 1)])
        mocked.get(
            api(CONFIG),
            payload={
                "app": {"colors": {"attendeePalette": {"0": "#a", "1": "#b", "2": "#c", "3": "#d"}}}
            },
        )
        mocked.post(api(PERSONS), status=201, payload=person("p3", color=2))
        created = await client.create_person("Kid")
        assert bodies(mocked, "POST", PERSONS) == [
            {
                "name": "Kid",
                "email": "",
                "colorIndex": 2,
                "settings": default_settings("").model_dump(by_alias=True),
            }
        ]
        assert created.id == "p3"

    @pytest.mark.parametrize(
        "config",
        [
            {"app": {"colors": {"attendeePalette": {"0": "#a", "1": "#b"}}}},  # every colour used
            {"app": {"colors": {}}},  # no palette
            {"app": None},
        ],
    )
    async def test_create_colour_falls_back_to_zero(self, client, mocked, no_sleep, config):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com", 0), person("p2", "", 1)])
        mocked.get(api(CONFIG), payload=config)
        mocked.post(api(PERSONS), status=201, payload=person("p3"))
        await client.create_person("Kid")
        assert bodies(mocked, "POST", PERSONS)[0]["colorIndex"] == 0

    async def test_create_with_email_uses_email_defaults(self, client, mocked):
        mocked.post(api(PERSONS), status=201, payload=person("p3", "k@x.com"))
        await client.create_person("Kid", email="k@x.com", color_index=0)
        assert bodies(mocked, "POST", PERSONS)[0]["settings"] == default_settings(
            "k@x.com"
        ).model_dump(by_alias=True)

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"name": "  "}, "Name cannot be empty"),
            ({"name": "Kid", "color_index": True}, "Invalid color index"),
            ({"name": "Kid", "color_index": -1}, "Invalid color index"),
            ({"name": "Kid", "email": None}, "Invalid email"),
            ({"name": "Kid", "settings": {"dailyAgenda": {}}}, "settings must be a PersonSettings"),
            (
                {
                    "name": "Kid",
                    "settings": PersonSettings(
                        calendar_item_reminder=NotificationChannel(email_enabled=True)
                    ),
                },
                "email address is needed",
            ),
        ],
    )
    async def test_create_validates_before_any_request(self, client, mocked, kwargs, message):
        before = count(mocked)
        with pytest.raises(CoziValidationError, match=message):
            await client.create_person(**kwargs)
        assert count(mocked) == before

    async def test_update_puts_the_four_keys_and_round_trips_unknown_flags(
        self, client, mocked, no_sleep
    ):
        settings = {
            **OFF,
            "calendarItemNotification": {
                "emailEnabled": False,
                "pushEnabled": True,
                "sendByDefault": False,
            },
        }
        current = person("p2", "", 3, settings=settings)
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com"), current])
        mocked.put(api(PERSONS + "p2"), payload={**current, "name": "Renamed", "version": 4})
        p = CoziPerson.model_validate(current)
        p.name = "Renamed"
        stored = await client.update_person(p)
        assert bodies(mocked, "PUT", PERSONS + "p2") == [
            {"name": "Renamed", "email": "", "colorIndex": 3, "settings": settings}
        ]
        assert (stored.name, stored.version) == ("Renamed", 4)

    async def test_update_refuses_to_clear_an_email(self, client, mocked):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com"), person("p2", "b@x.com")])
        p = CoziPerson.model_validate(person("p2", "b@x.com"))
        p.email = ""
        with pytest.raises(CoziValidationError, match="Cannot remove a member.s email"):
            await client.update_person(p)
        assert sent(mocked, "PUT", PERSONS + "p2") == []

    async def test_update_unknown_member_sends_no_put(self, client, mocked):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com")])
        with pytest.raises(ResourceNotFoundError):
            await client.update_person(CoziPerson.model_validate(person("gone")))
        assert sent(mocked, "PUT", PERSONS + "gone") == []

    @pytest.mark.parametrize(
        "change, message",
        [
            ({"id": "../x"}, "Invalid person_id"),
            ({"settings": None}, "settings must be a PersonSettings"),
            ({"color": None}, "Invalid color index"),
        ],
    )
    async def test_update_validates_before_any_request(self, client, mocked, change, message):
        p = CoziPerson.model_validate(person("p2")).model_copy(update=change)
        before = count(mocked)
        with pytest.raises(CoziValidationError, match=message):
            await client.update_person(p)
        assert count(mocked) == before

    async def test_delete_sends_delete_when_allowed(self, client, mocked, no_sleep):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com"), person("p2", "")])
        mocked.delete(api(PERSONS + "p2"), status=204)
        await client.delete_person("p2")
        assert len(sent(mocked, "DELETE", PERSONS + "p2")) == 1
        assert "json" not in sent(mocked, "DELETE", PERSONS + "p2")[0]

    @pytest.mark.parametrize(
        "members, target, message",
        [
            ([person("p1", "a@x.com")], "p1", "at least one family member"),
            ([person("p1", "")], "p1", "at least one family member"),
            ([person("p1", "a@x.com"), person("p2", "")], "p1", "only member with an email"),
        ],
    )
    async def test_delete_guards_send_no_delete(self, client, mocked, members, target, message):
        mocked.get(api(PERSONS), payload=members)
        with pytest.raises(CoziValidationError, match=message):
            await client.delete_person(target)
        assert sent(mocked, "DELETE", PERSONS + target) == []

    async def test_delete_one_of_two_email_holders_is_allowed(self, client, mocked, no_sleep):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com"), person("p2", "b@x.com")])
        mocked.delete(api(PERSONS + "p1"), status=204)
        await client.delete_person("p1")
        assert len(sent(mocked, "DELETE", PERSONS + "p1")) == 1

    async def test_delete_unknown_member(self, client, mocked):
        mocked.get(api(PERSONS), payload=[person("p1", "a@x.com"), person("p2")])
        with pytest.raises(ResourceNotFoundError):
            await client.delete_person("p3")

    async def test_delete_bad_id_before_any_request(self, client, mocked):
        before = count(mocked)
        with pytest.raises(CoziValidationError):
            await client.delete_person("../x")
        assert count(mocked) == before

    async def test_send_verification_email(self, client, mocked):
        mocked.post(api(PERSONS + "p1/verify-email"), status=204)
        await client.send_verification_email("p1")
        (kw,) = sent(mocked, "POST", PERSONS + "p1/verify-email")
        assert kw["data"] == b""
        assert kw["headers"]["Content-Type"] == "application/json; charset=utf-8"
        assert "json" not in kw


class TestHousehold:
    async def test_get_household(self, client, mocked):
        mocked.get(api(HOUSEHOLD), payload=household())
        h = await client.get_household()
        assert (h.account_id, h.name, h.color_index, h.timezone) == (
            "acct-123",
            "Home",
            1,
            "America/New_York",
        )

    @pytest.mark.parametrize(
        "kwargs, body",
        [
            ({"name": " Smiths "}, {"name": " Smiths "}),
            ({"color_index": 4}, {"colorIndex": 4}),
            ({"timezone": "Europe/London"}, {"timezone": "Europe/London"}),
            (
                {"name": "S", "color_index": 0, "timezone": "UTC"},
                {"name": "S", "colorIndex": 0, "timezone": "UTC"},
            ),
        ],
    )
    async def test_update_sends_only_the_given_keys(self, client, mocked, kwargs, body):
        mocked.put(api(HOUSEHOLD), payload=household(name="stored"))
        h = await client.update_household(**kwargs)
        assert bodies(mocked, "PUT", HOUSEHOLD) == [body]
        assert h.name == "stored"

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({}, "Nothing to update"),
            ({"name": ""}, "name cannot be empty"),
            ({"color_index": False}, "Invalid color index"),
            ({"timezone": " "}, "IANA"),
        ],
    )
    async def test_update_validates_before_any_request(self, client, mocked, kwargs, message):
        before = count(mocked)
        with pytest.raises(CoziValidationError, match=message):
            await client.update_household(**kwargs)
        assert count(mocked) == before


class TestHouseholdPhoto:
    async def test_upload_then_put_photo(self, client, mocked, no_sleep):
        photo = {"id": "ph1", "thumbUrl": "https://img/t.jpg", "isDefault": False}
        mocked.post(api(UPLOAD), payload=photo)
        mocked.put(api(HOUSEHOLD), payload=household(photo=photo))
        h = await client.upload_household_photo(b"\x89PNG data", "/home/me/pics/Family.PNG")
        (kw,) = sent(mocked, "POST", UPLOAD)
        assert "Authorization" not in kw["headers"]
        assert "json" not in kw
        assert sent_query(mocked, "POST", UPLOAD)[0]["auth"] == TEST_ACCESS_TOKEN
        form = kw["data"]
        assert isinstance(form, aiohttp.FormData)
        ((options, headers, value),) = form._fields
        assert (options["name"], options["filename"], headers["Content-Type"], value) == (
            "photopicker",
            "Family.PNG",
            "image/png",
            b"\x89PNG data",
        )
        assert bodies(mocked, "PUT", HOUSEHOLD) == [{"photo": photo}]
        assert h.photo == photo

    async def test_replay_after_401_carries_the_new_token(self, client, mocked, no_sleep):
        # aioresponses deep-copies what it records, so the fresh-FormData-per-attempt
        # half of this lives in test_contract_account (a reused FormData raises there).
        mocked.post(api(UPLOAD), status=401, payload={"error": "expired"})
        mocked.post(
            api(REAUTH_PATH),
            payload={"accessToken": "tok-2", "accountId": "acct-123", "expiresIn": 3600},
        )
        mocked.post(api(UPLOAD), payload={"id": "ph1"})
        mocked.put(api(HOUSEHOLD), payload=household(photo={"id": "ph1"}))
        await client.upload_household_photo(b"jpeg", "a.jpg")
        assert [q["auth"] for q in sent_query(mocked, "POST", UPLOAD)] == [
            TEST_ACCESS_TOKEN,
            "tok-2",
        ]

    async def test_non_object_upload_response_sends_no_put(self, client, mocked):
        mocked.post(api(UPLOAD), status=204)
        with pytest.raises(ResponseParseError):
            await client.upload_household_photo(b"jpeg", "a.jpeg")
        assert sent(mocked, "PUT", HOUSEHOLD) == []

    @pytest.mark.parametrize(
        "data, filename, message",
        [
            (b"", "a.jpg", "non-empty bytes"),
            ("text", "a.jpg", "non-empty bytes"),
            (b"x" * (20 * 1024 * 1024 + 1), "a.jpg", "larger than 20 MB"),
            (b"x", "a.gif", ".jpg, .jpeg or .png"),
            (b"x", None, "filename"),
        ],
    )
    async def test_upload_validates_before_any_request(
        self, client, mocked, data, filename, message
    ):
        before = count(mocked)
        with pytest.raises(CoziValidationError, match=message):
            await client.upload_household_photo(data, filename)
        assert count(mocked) == before


class TestReads:
    @pytest.mark.parametrize(
        "method, path, payload",
        [
            ("get_config", CONFIG, {"featureKeys": ["SmartAdd"]}),
            ("get_personal_info", "/2111/{acct}/account/pii/", {"familyName": "x"}),
            ("get_subscriptions", "/2004/{acct}/subscription/", [{"entitlementId": "cozi_gold"}]),
        ],
    )
    async def test_opaque_reads(self, client, mocked, method, path, payload):
        mocked.get(api(path), payload=payload)
        assert await getattr(client, method)() == payload
        assert len(sent(mocked, "GET", path)) == 1

    @pytest.mark.parametrize(
        "method, path, payload",
        [
            ("get_config", CONFIG, []),
            ("get_personal_info", "/2111/{acct}/account/pii/", None),
            ("get_subscriptions", "/2004/{acct}/subscription/", {"a": 1}),
        ],
    )
    async def test_opaque_reads_reject_the_wrong_shape(self, client, mocked, method, path, payload):
        if payload is None:
            mocked.get(api(path), status=204)  # empty body decodes to True
        else:
            mocked.get(api(path), payload=payload)
        with pytest.raises(ResponseParseError):
            await getattr(client, method)()


class TestIcalFeeds:
    async def test_get_ical_feeds(self, client, mocked):
        mocked.get(
            api("/2111/{acct}/icalendar/settings/"),
            payload={
                "feeds": {
                    FAMILY_FEED_ID: {"status": "enabled", "url": "webcal://s"},
                    "p1": {"status": "revoked"},
                },
                "scheduleFeeds": {},
            },
        )
        feeds = await client.get_ical_feeds()
        assert {k: (f.status, f.url) for k, f in feeds.items()} == {
            FAMILY_FEED_ID: ("enabled", "webcal://s"),
            "p1": ("revoked", None),
        }

    async def test_bad_feed_error_carries_no_feed_url(self, client, mocked):
        mocked.get(
            api("/2111/{acct}/icalendar/settings/"),
            payload={"feeds": {"p1": {"url": "webcal://secret-token"}}},  # no status
        )
        with pytest.raises(ResponseParseError) as info:
            await client.get_ical_feeds()
        e = info.value
        assert (e.response_data, e.__cause__, e.__suppress_context__) == ({}, None, True)
        assert "secret-token" not in str(e)

    async def test_get_ical_feeds_without_feeds_is_parse_error(self, client, mocked):
        mocked.get(api("/2111/{acct}/icalendar/settings/"), payload={"scheduleFeeds": {}})
        with pytest.raises(ResponseParseError):
            await client.get_ical_feeds()

    @pytest.mark.parametrize("enabled, status", [(True, "enabled"), (False, "revoked")])
    async def test_set_ical_feed(self, client, mocked, enabled, status):
        path = f"/2111/{{acct}}/icalendar/settings/attendee/{FAMILY_FEED_ID}"
        mocked.put(api(path), status=204)
        await client.set_ical_feed(FAMILY_FEED_ID, enabled)
        assert bodies(mocked, "PUT", path) == [{"feed": {"status": status}}]

    @pytest.mark.parametrize("person_id, enabled", [("p1", "false"), ("p1", 1), ("../p", True)])
    async def test_set_ical_feed_validates_before_any_request(
        self, client, mocked, person_id, enabled
    ):
        before = count(mocked)
        with pytest.raises(CoziValidationError):
            await client.set_ical_feed(person_id, enabled)
        assert count(mocked) == before


class TestSharedCalendars:
    async def test_get_subscribed_calendars_uses_the_unversioned_base(self, client, mocked):
        mocked.get(
            api(SHARED, root=UNVERSIONED),
            payload=[{"calendar": {"id": US, "name": "US Holidays"}, "attendees": []}],
        )
        subs = await client.get_subscribed_calendars()
        assert [(s.calendar.id, s.calendar.name, s.attendees) for s in subs] == [
            (US, "US Holidays", [])
        ]
        assert len(sent(mocked, "GET", SHARED, root=UNVERSIONED)) == 1

    async def test_bad_subscription_error_carries_no_calendar_url(self, client, mocked):
        mocked.get(
            api(SHARED, root=UNVERSIONED),
            payload=[{"calendar": {"url": "https://private/secret.ics"}, "attendees": []}],
        )
        with pytest.raises(ResponseParseError) as info:
            await client.get_subscribed_calendars()
        e = info.value
        assert (e.response_data, e.__cause__, e.__suppress_context__) == ({}, None, True)
        assert "secret" not in str(e)

    @pytest.mark.parametrize("attendees, body", [((), []), (["p1", "p2"], ["p1", "p2"])])
    async def test_subscribe_puts_a_bare_array(self, client, mocked, attendees, body):
        mocked.put(api(SHARED + US + "/", root=UNVERSIONED), payload={"ok": True})
        await client.subscribe_calendar(US, attendees)
        assert bodies(mocked, "PUT", SHARED + US + "/", root=UNVERSIONED) == [body]

    async def test_unsubscribe_is_a_bodyless_delete(self, client, mocked):
        mocked.delete(api(SHARED + US + "/", root=UNVERSIONED), status=204)
        await client.unsubscribe_calendar(US)
        (kw,) = sent(mocked, "DELETE", SHARED + US + "/", root=UNVERSIONED)
        assert "json" not in kw and "data" not in kw

    async def test_add_internet_calendar_registers_then_subscribes(self, client, mocked, no_sleep):
        mocked.post(
            api("/calendar/sharedcalendar/internetcalendar/", root=UNVERSIONED),
            payload={"calendar_source_id": "src1"},
        )
        mocked.put(api(SHARED + "src1/", root=UNVERSIONED), payload={"ok": True})
        assert await client.add_internet_calendar(" https://example.com/a.ics ", ["p1"]) == "src1"
        assert bodies(
            mocked, "POST", "/calendar/sharedcalendar/internetcalendar/", root=UNVERSIONED
        ) == [{"url": "https://example.com/a.ics"}]
        assert bodies(mocked, "PUT", SHARED + "src1/", root=UNVERSIONED) == [["p1"]]

    async def test_add_internet_calendar_bad_source_id_is_not_subscribed(self, client, mocked):
        mocked.post(
            api("/calendar/sharedcalendar/internetcalendar/", root=UNVERSIONED),
            payload={"calendar_source_id": "../x"},
        )
        with pytest.raises(ResponseParseError, match="was not subscribed"):
            await client.add_internet_calendar("https://example.com/a.ics")
        assert sent(mocked, "PUT", SHARED + "../x/", root=UNVERSIONED) == []
        assert count(mocked) == 2  # the login and the POST

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.subscribe_calendar(US, ["ok", "../bad"]),
            lambda c: c.subscribe_calendar(US, "p1"),
            lambda c: c.subscribe_calendar(US, None),
            lambda c: c.add_internet_calendar("https://example.com/a.ics", 5),
            lambda c: c.subscribe_calendar("../x"),
            lambda c: c.unsubscribe_calendar("x/y"),
            lambda c: c.add_internet_calendar("https://example.com/a.ics", ["../bad"]),
            lambda c: c.add_internet_calendar("  "),
        ],
    )
    async def test_validation_before_any_request(self, client, mocked, call):
        before = count(mocked)
        with pytest.raises(CoziValidationError):
            await call(client)
        assert count(mocked) == before
