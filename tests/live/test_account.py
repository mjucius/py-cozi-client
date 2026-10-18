"""
Live: account reads, a throwaway member's lifecycle, and a household rename that is restored.

Nothing here prints account data: assertions compare into a bool first, so a failure
shows key names or ``False``, never a name, email, pii, subscription or feed URL.
Not run live (unit and contract only): verify-email, set_ical_feed, subscribe and
unsubscribe, add_internet_calendar, upload_household_photo.
"""

import pytest

from cozi_client import ID_PATTERN
from cozi_client.account import FAMILY_FEED_ID, EmailStatus, PersonSettings
from tests.live.conftest import domain_sweeper, owned_by, subject

_sweep = domain_sweeper("account")

PERSONS = "/2004/{acct}/account/person/"
# Every member's settings carry exactly these keys (observed 2026-10-16). A new key
# here is a flag update_person must keep round-tripping.
SETTINGS_KEYS = {
    "calendarItemNotification": ["emailEnabled", "pushEnabled", "sendByDefault"],
    "calendarItemReminder": ["emailEnabled", "pushEnabled"],
    "dailyAgenda": ["emailEnabled"],
    "weeklyAgenda": ["emailEnabled"],
}


async def test_family_members_parse_with_typed_settings(live_client):
    members = await live_client.get_family_members()
    ok = bool(members) and all(
        ID_PATTERN.fullmatch(m.id)
        and isinstance(m.settings, PersonSettings)
        and type(m.email_status) is EmailStatus
        for m in members
    )
    assert ok, "a member did not parse with an id, typed settings and a known email status"
    raw = await live_client._request("GET", PERSONS)
    shapes = [{k: sorted(v) for k, v in p["settings"].items()} for p in raw]
    assert all(s == SETTINGS_KEYS for s in shapes), shapes  # key names only


async def test_household_reads(live_client):
    h = await live_client.get_household()
    ok = (
        h.account_id == live_client.account_id
        and isinstance(h.name, str)
        and isinstance(h.timezone, str)
        and isinstance(h.color_index, int)
        and isinstance(h.photo, dict)
    )
    assert ok, "household shape changed"


async def test_config_has_the_attendee_palette(live_client):
    config = await live_client.get_config()
    palette = config["app"]["colors"]["attendeePalette"]
    assert (type(palette).__name__, len(palette)) == ("dict", 16)
    assert isinstance(config["featureKeys"], list)


async def test_personal_info_shape(live_client):
    info = await live_client.get_personal_info()
    assert sorted(info) == ["devices", "externalIds", "familyName", "names"]


async def test_subscriptions_shape(live_client):
    subs = await live_client.get_subscriptions()
    ok = isinstance(subs, list) and all(isinstance(s, dict) for s in subs)
    assert ok, "subscriptions are not a list of objects"


async def test_ical_feed_statuses(live_client):
    feeds = await live_client.get_ical_feeds()
    member_ids = {m.id for m in await live_client.get_family_members()}
    ok = (
        FAMILY_FEED_ID in feeds
        and set(feeds) <= member_ids | {FAMILY_FEED_ID}
        and all(f.status in {"enabled", "disabled", "revoked", "unset"} for f in feeds.values())
    )
    assert ok, "iCal feeds are not keyed by member ids plus the family id, or a status is unknown"


async def test_subscribed_calendars_parse(live_client):
    subs = await live_client.get_subscribed_calendars()
    ok = all(
        ID_PATTERN.fullmatch(s.calendar.id) and all(ID_PATTERN.fullmatch(a) for a in s.attendees)
        for s in subs
    )
    assert ok, "a subscribed calendar or attendee id is malformed"


async def test_person_create_update_delete(live_client):
    raw = await live_client._request("GET", PERSONS)
    config = await live_client._request("GET", "/2004/{acct}/config/")
    used = {p["colorIndex"] for p in raw}
    size = len(config["app"]["colors"]["attendeePalette"])
    expected_color = next((i for i in range(size) if i not in used), 0)

    name = subject("account")
    created = await live_client.create_person(name, email="", settings=PersonSettings())
    try:
        # Cozi stores email "" as null, adds sendByDefault: true, and reports
        # "not verified" for a member without an email (observed 2026-10-16).
        assert (created.name, created.email, created.color) == (name, None, expected_color)
        assert created.email_status is EmailStatus.NOT_VERIFIED
        assert created.settings.model_dump(by_alias=True) == {
            "calendarItemNotification": {
                "emailEnabled": False,
                "pushEnabled": False,
                "sendByDefault": True,
            },
            "calendarItemReminder": {"emailEnabled": False, "pushEnabled": False},
            "dailyAgenda": {"emailEnabled": False},
            "weeklyAgenda": {"emailEnabled": False},
        }
        stored = [m for m in await live_client.get_family_members() if m.id == created.id]
        assert stored == [created]

        edit = created.model_copy(deep=True)
        edit.name = name + " renamed"
        edit.settings.calendar_item_reminder.push_enabled = True
        edit.settings.calendar_item_notification.sendByDefault = False  # an unmodelled flag
        updated = await live_client.update_person(edit)
        assert (updated.name, updated.email, updated.color) == (
            name + " renamed",
            None,
            expected_color,
        )
        assert updated.settings.model_dump(by_alias=True) == {
            "calendarItemNotification": {
                "emailEnabled": False,
                "pushEnabled": False,
                "sendByDefault": False,
            },
            "calendarItemReminder": {"emailEnabled": False, "pushEnabled": True},
            "dailyAgenda": {"emailEnabled": False},
            "weeklyAgenda": {"emailEnabled": False},
        }
        stored = [m for m in await live_client.get_family_members() if m.id == created.id]
        assert stored == [updated]
    finally:
        await live_client.delete_person(created.id)
    remaining = {m.id for m in await live_client.get_family_members()}
    assert created.id not in remaining


async def test_household_rename_is_applied_and_restored(live_client):
    before = await live_client.get_household()
    original = before.name
    if not original.strip():
        pytest.skip("the household has no name, which update_household cannot restore")
    # A run killed mid-rename leaves this; restoring it would make the residue permanent.
    assert not owned_by("account", original), "household still has a test name: rename it by hand"
    new_name = subject("account")
    renamed = reread = None
    try:
        renamed = await live_client.update_household(name=new_name)
        reread = await live_client.get_household()
    finally:
        restored = await live_client.update_household(name=original)  # verbatim, never stripped
    after = await live_client.get_household()
    ok_restored = restored.name == original and after == before
    assert ok_restored, "household NOT restored to its original state"
    ok_applied = (
        renamed.name == new_name
        and reread.name == new_name
        and (reread.color_index, reread.timezone, reread.cobrand)
        == (before.color_index, before.timezone, before.cobrand)
    )
    assert ok_applied, "the household rename was not applied as sent"
