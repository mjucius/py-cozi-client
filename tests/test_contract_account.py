"""Account endpoints over a real socket (tests/stub_cozi.py): exact paths, bases and auth modes."""

from cozi_client.account import FAMILY_FEED_ID, HOLIDAY_CALENDARS, CoziPerson, default_settings

PERSONS = "/2004/{acct}/account/person/"
HOUSEHOLD = "/2004/{acct}/account/"
SHARED = "/calendar/sharedcalendar/{acct}/"
INTERNET = "/calendar/sharedcalendar/internetcalendar/"
UPLOAD = "/2111/{acct}/photo/uploader.json"
US = HOLIDAY_CALENDARS["US"]

OFF = {
    "calendarItemNotification": {
        "emailEnabled": False,
        "pushEnabled": False,
        "sendByDefault": True,
    },
    "calendarItemReminder": {"emailEnabled": False, "pushEnabled": False},
    "dailyAgenda": {"emailEnabled": False},
    "weeklyAgenda": {"emailEnabled": False},
}
OWNER = {
    "accountPersonId": "p1",
    "name": "Owner",
    "email": "o@example.com",
    "colorIndex": 0,
    "settings": OFF,
}
KID = {"accountPersonId": "p2", "name": "Kid", "email": None, "colorIndex": 2, "settings": OFF}


async def test_person_lifecycle_paths_and_bodies(stub):
    stub.route("GET", PERSONS, json=[OWNER, KID])
    stub.route(
        "GET",
        "/2004/{acct}/config/",
        json={"app": {"colors": {"attendeePalette": {"0": "a", "1": "b", "2": "c"}}}},
    )
    stub.route("POST", PERSONS, json={**KID, "accountPersonId": "p3", "colorIndex": 1}, status=201)
    stub.route("PUT", PERSONS + "p2", json={**KID, "name": "Kid 2"})
    stub.route("DELETE", PERSONS + "p2")
    stub.route("POST", PERSONS + "p2/verify-email")
    async with stub.client() as cozi:
        created = await cozi.create_person("New")
        kid = CoziPerson.model_validate(KID)
        kid.name = "Kid 2"
        updated = await cozi.update_person(kid)
        await cozi.send_verification_email("p2")
        await cozi.delete_person("p2")
    assert (created.id, updated.name) == ("p3", "Kid 2")
    assert stub.sent("POST", PERSONS)[0]["json"] == {
        "name": "New",
        "email": "",
        "colorIndex": 1,
        "settings": default_settings("").model_dump(by_alias=True),
    }
    assert stub.sent("PUT", PERSONS + "p2")[0]["json"] == {
        "name": "Kid 2",
        "email": "",
        "colorIndex": 2,
        "settings": OFF,
    }
    verify = stub.sent("POST", PERSONS + "p2/verify-email")[0]
    assert (verify["body"], verify["content_type"]) == (b"", "application/json")
    delete = stub.sent("DELETE", PERSONS + "p2")[0]
    assert delete["body"] == b""


async def test_household_partial_put(stub):
    stored = {
        "accountId": stub.ACCOUNT,
        "name": "Smiths",
        "colorIndex": 4,
        "timezone": "Europe/London",
    }
    stub.route("GET", HOUSEHOLD, json=stored)
    stub.route("PUT", HOUSEHOLD, json=stored)
    async with stub.client() as cozi:
        assert (await cozi.get_household()).name == "Smiths"
        h = await cozi.update_household(color_index=4)
    assert h.color_index == 4
    assert stub.sent("PUT", HOUSEHOLD)[0]["json"] == {"colorIndex": 4}


async def test_photo_upload_uses_query_token_and_multipart(stub):
    photo = {"id": "ph1", "thumbUrl": "https://img/t.jpg"}
    stub.route("POST", UPLOAD, json=photo, auth="query")
    stub.route("PUT", HOUSEHOLD, json={"accountId": stub.ACCOUNT, "name": "S", "photo": photo})
    async with stub.client() as cozi:
        h = await cozi.upload_household_photo(b"\xff\xd8jpegbytes", "family.jpg")
    upload = stub.sent("POST", UPLOAD)[0]
    assert "Authorization" not in upload["headers"]
    assert upload["query"]["auth"] == stub.token
    assert upload["content_type"] == "multipart/form-data"
    assert b'name="photopicker"' in upload["body"]
    assert b'filename="family.jpg"' in upload["body"]
    assert b"\xff\xd8jpegbytes" in upload["body"]
    assert stub.sent("PUT", HOUSEHOLD)[0]["json"] == {"photo": photo}
    assert h.photo == photo


async def test_photo_upload_replayed_after_401_with_a_fresh_form(stub):
    # aiohttp refuses to send a FormData twice, so this fails if the form is reused.
    stub.route("POST", UPLOAD, json={"id": "ph1"}, auth="query")
    stub.route("PUT", HOUSEHOLD, json={"accountId": stub.ACCOUNT, "photo": {"id": "ph1"}})
    async with stub.client() as cozi:
        await cozi.authenticate()
        stub.token = "rotated-elsewhere"  # the first upload attempt gets 401
        await cozi.upload_household_photo(b"png", "a.png")
    first, second = stub.sent("POST", UPLOAD)
    assert (first["query"]["auth"], second["query"]["auth"]) == ("tok-1", "tok-2")
    assert b'name="photopicker"' in second["body"] and second["body"].endswith(b"--\r\n")
    assert stub.reauths == 1


async def test_reads_on_their_versioned_paths(stub):
    stub.route("GET", "/2004/{acct}/config/", json={"featureKeys": []})
    stub.route("GET", "/2111/{acct}/account/pii/", json={"familyName": "x"})
    stub.route("GET", "/2004/{acct}/subscription/", json=[])
    stub.route(
        "GET",
        "/2111/{acct}/icalendar/settings/",
        json={"feeds": {FAMILY_FEED_ID: {"status": "unset"}}},
    )
    stub.route("PUT", f"/2111/{{acct}}/icalendar/settings/attendee/{FAMILY_FEED_ID}")
    async with stub.client() as cozi:
        assert await cozi.get_config() == {"featureKeys": []}
        assert await cozi.get_personal_info() == {"familyName": "x"}
        assert await cozi.get_subscriptions() == []
        assert (await cozi.get_ical_feeds())[FAMILY_FEED_ID].status == "unset"
        await cozi.set_ical_feed(FAMILY_FEED_ID, True)
    put = stub.sent("PUT", f"/2111/{{acct}}/icalendar/settings/attendee/{FAMILY_FEED_ID}")[0]
    assert put["json"] == {"feed": {"status": "enabled"}}


async def test_shared_calendars_on_the_unversioned_base(stub):
    stub.route(
        "GET",
        SHARED,
        json=[{"calendar": {"id": US, "name": "US"}, "attendees": ["p1"]}],
        unversioned=True,
    )
    stub.route("PUT", f"{SHARED}{US}/", json={"ok": True}, unversioned=True)
    stub.route("DELETE", f"{SHARED}{US}/", unversioned=True)
    stub.route("POST", INTERNET, json={"calendar_source_id": "src1"}, unversioned=True)
    stub.route("PUT", f"{SHARED}src1/", json={"ok": True}, unversioned=True)
    async with stub.client() as cozi:
        subs = await cozi.get_subscribed_calendars()
        await cozi.subscribe_calendar(US)
        await cozi.unsubscribe_calendar(US)
        source = await cozi.add_internet_calendar("https://example.com/c.ics", ["p1"])
    assert [(s.calendar.id, s.attendees) for s in subs] == [(US, ["p1"])]
    assert stub.sent("PUT", f"{SHARED}{US}/", unversioned=True)[0]["json"] == []
    assert stub.sent("DELETE", f"{SHARED}{US}/", unversioned=True)[0]["body"] == b""
    assert stub.sent("POST", INTERNET, unversioned=True)[0]["json"] == {
        "url": "https://example.com/c.ics"
    }
    assert source == "src1"
    assert stub.sent("PUT", f"{SHARED}src1/", unversioned=True)[0]["json"] == ["p1"]
