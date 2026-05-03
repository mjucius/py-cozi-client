"""Tests for CoziClient using aioresponses to mock aiohttp."""

import asyncio
import re
import time as time_module
from datetime import date
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

from cozi_client import CoziClient
from exceptions import (
    APIError,
    AuthenticationError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    ResourceNotFoundError,
    ValidationError,
)
from models import CoziAppointment, ItemStatus, ListType
from tests.conftest import (
    AUTH_URL,
    TEST_ACCESS_TOKEN,
    TEST_ACCOUNT_ID,
    TEST_PASSWORD,
    TEST_USERNAME,
)


def _api(path: str) -> str:
    return f"https://rest.cozi.com/api/ext/2004/{TEST_ACCOUNT_ID}{path}"


@pytest.fixture
def no_sleep():
    """Patch asyncio.sleep so retry/rate-limit waits don't slow tests."""
    with patch("cozi_client.asyncio.sleep", new=AsyncMock()) as m:
        yield m


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    async def test_happy_path(self, mocked, auth_response):
        mocked.post(AUTH_URL, payload=auth_response)
        async with CoziClient(TEST_USERNAME, TEST_PASSWORD) as client:
            await client.authenticate()
            assert client._access_token == TEST_ACCESS_TOKEN
            assert client._account_id == TEST_ACCOUNT_ID
            assert client._authenticated is True

    async def test_invalid_response_raises(self, mocked):
        mocked.post(AUTH_URL, payload={"accessToken": "x"})  # missing accountId
        async with CoziClient(TEST_USERNAME, TEST_PASSWORD) as client:
            with pytest.raises(AuthenticationError):
                await client.authenticate()

    async def test_401_raises_auth_error(self, mocked, no_sleep):
        # Auth request itself doesn't retry on 401 because require_auth=False
        # so the 401 path can't recurse — but the response body is parsed.
        mocked.post(AUTH_URL, status=401, payload={"error": "bad creds"})
        async with CoziClient(TEST_USERNAME, TEST_PASSWORD, retry_attempts=1) as client:
            with pytest.raises(AuthenticationError):
                await client.authenticate()

    async def test_logout_clears_state(self, client):
        assert client._authenticated is True
        await client.logout()
        assert client._authenticated is False
        assert client._access_token is None
        assert client._account_id is None
        assert client._token_expires is None


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    async def test_owned_session_closed_on_exit(self, mocked, auth_response):
        mocked.post(AUTH_URL, payload=auth_response)
        async with CoziClient(TEST_USERNAME, TEST_PASSWORD) as client:
            await client.authenticate()
            session = client._session
            assert session is not None
            assert session.closed is False
        # After exit
        assert client._session is None
        assert session.closed is True

    async def test_external_session_not_closed(self, mocked, auth_response):
        mocked.post(AUTH_URL, payload=auth_response)
        external = aiohttp.ClientSession()
        try:
            async with CoziClient(
                TEST_USERNAME, TEST_PASSWORD, session=external
            ) as client:
                await client.authenticate()
            assert external.closed is False
        finally:
            await external.close()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    async def test_404_raises_resource_not_found(self, client, mocked):
        mocked.get(_api("/list/"), status=404, payload={"error": "missing"})
        with pytest.raises(ResourceNotFoundError) as exc:
            await client.get_lists()
        assert exc.value.status_code == 404

    async def test_403_raises_permission_denied(self, client, mocked):
        mocked.get(_api("/list/"), status=403, payload={"error": "forbidden"})
        with pytest.raises(PermissionDeniedError) as exc:
            await client.get_lists()
        assert exc.value.status_code == 403

    async def test_400_raises_api_error(self, client, mocked):
        mocked.get(_api("/list/"), status=400, payload={"error": "bad"})
        with pytest.raises(APIError) as exc:
            await client.get_lists()
        # APIError is generic; ensure it's not a more-specific subclass
        assert not isinstance(exc.value, (ResourceNotFoundError, PermissionDeniedError))

    async def test_500_retries_then_raises(self, client, mocked, no_sleep):
        mocked.get(_api("/list/"), status=500, payload={"error": "boom"})
        mocked.get(_api("/list/"), status=500, payload={"error": "boom"})
        with pytest.raises(APIError) as exc:
            await client.get_lists()
        assert exc.value.status_code == 500
        # retry_attempts=2 → one retry, so two sleeps total: rate-limit (maybe)
        # plus one backoff from 5xx retry. Asserting >=1 sleep covers it.
        assert no_sleep.await_count >= 1

    async def test_503_retried(self, client, mocked, no_sleep):
        # First call 503, second call 200
        mocked.get(_api("/list/"), status=503, payload={"error": "down"})
        mocked.get(_api("/list/"), payload=[])
        result = await client.get_lists()
        assert result == []

    async def test_429_retries_then_raises(self, client, mocked, no_sleep):
        mocked.get(_api("/list/"), status=429, payload={"error": "slow down"})
        mocked.get(_api("/list/"), status=429, payload={"error": "slow down"})
        with pytest.raises(RateLimitError):
            await client.get_lists()

    async def test_429_then_success(self, client, mocked, no_sleep):
        mocked.get(_api("/list/"), status=429, payload={})
        mocked.get(_api("/list/"), payload=[])
        result = await client.get_lists()
        assert result == []

    async def test_network_error_raises(self, client, mocked, no_sleep):
        mocked.get(_api("/list/"), exception=aiohttp.ClientConnectionError("oops"))
        mocked.get(_api("/list/"), exception=aiohttp.ClientConnectionError("oops"))
        with pytest.raises(NetworkError):
            await client.get_lists()


# ---------------------------------------------------------------------------
# Re-auth on mid-session 401
# ---------------------------------------------------------------------------


class TestReauthOn401:
    async def test_401_triggers_reauth_and_retry(
        self, client, mocked, auth_response, no_sleep
    ):
        # First list fetch returns 401, then auth is re-issued, then 200.
        mocked.get(_api("/list/"), status=401, payload={"error": "expired"})
        mocked.post(AUTH_URL, payload=auth_response)
        mocked.get(_api("/list/"), payload=[])
        result = await client.get_lists()
        assert result == []


# ---------------------------------------------------------------------------
# Rate limiting interval
# ---------------------------------------------------------------------------


class TestRateLimitInterval:
    async def test_min_interval_enforced(self, client, mocked):
        mocked.get(_api("/list/"), payload=[])
        mocked.get(_api("/list/"), payload=[])
        start = time_module.monotonic()
        await client.get_lists()
        await client.get_lists()
        elapsed = time_module.monotonic() - start
        # _min_request_interval is 0.1; second call must wait
        assert elapsed >= 0.1


# ---------------------------------------------------------------------------
# CRUD smoke tests — request shape + response parsing
# ---------------------------------------------------------------------------


class TestListsCRUD:
    async def test_get_lists(self, client, mocked):
        mocked.get(
            _api("/list/"),
            payload=[
                {
                    "listId": "l1",
                    "title": "Groceries",
                    "listType": "shopping",
                    "items": [],
                },
            ],
        )
        lists = await client.get_lists()
        assert len(lists) == 1
        assert lists[0].title == "Groceries"
        assert lists[0].list_type == "shopping"

    async def test_get_lists_by_type_filters(self, client, mocked):
        mocked.get(
            _api("/list/"),
            payload=[
                {
                    "listId": "l1",
                    "title": "Groceries",
                    "listType": "shopping",
                    "items": [],
                },
                {"listId": "l2", "title": "Chores", "listType": "todo", "items": []},
            ],
        )
        result = await client.get_lists_by_type(ListType.SHOPPING)
        assert len(result) == 1
        assert result[0].title == "Groceries"

    async def test_create_list(self, client, mocked):
        mocked.post(
            _api("/list/"),
            payload={"listId": "new", "title": "Trip", "listType": "todo", "items": []},
        )
        result = await client.create_list("Trip", ListType.TODO)
        assert result.id == "new"
        assert result.title == "Trip"

    async def test_create_list_empty_title_raises(self, client):
        with pytest.raises(ValidationError):
            await client.create_list("   ", ListType.TODO)

    async def test_delete_list(self, client, mocked):
        mocked.delete(_api("/list/l1"), status=204)
        assert await client.delete_list("l1") is True


class TestItemsCRUD:
    async def test_add_item(self, client, mocked):
        mocked.post(
            _api("/list/l1/item/"),
            payload={"itemId": "i1", "text": "Milk", "status": "incomplete"},
        )
        item = await client.add_item("l1", "Milk")
        assert item.id == "i1"
        assert item.text == "Milk"

    async def test_add_item_empty_text_raises(self, client):
        with pytest.raises(ValidationError):
            await client.add_item("l1", "  ")

    async def test_mark_item(self, client, mocked):
        mocked.put(
            _api("/list/l1/item/i1"),
            payload={"itemId": "i1", "text": "Milk", "status": "complete"},
        )
        item = await client.mark_item("l1", "i1", ItemStatus.COMPLETE)
        assert item.status == "complete"

    async def test_remove_items_empty_list_short_circuits(self, client):
        # No mock registered — if it tried to hit the API, aioresponses would raise.
        assert await client.remove_items("l1", []) is True


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


class TestCalendar:
    async def test_get_calendar_validates_month(self, client):
        with pytest.raises(ValidationError):
            await client.get_calendar(2026, 13)

    async def test_get_calendar_parses_items_format(self, client, mocked):
        mocked.get(
            _api("/calendar/2026/5"),
            payload={
                "items": {
                    "a1": {
                        "id": "a1",
                        "day": "2026-05-02",
                        "description": "Soccer",
                        "startTime": "14:30:00",
                        "endTime": "15:30:00",
                        "householdMembers": ["Alice"],
                        "dateSpan": 0,
                        "itemDetails": {"location": "Field A"},
                    }
                }
            },
        )
        result = await client.get_calendar(2026, 5)
        assert len(result) == 1
        assert result[0].subject == "Soccer"
        assert result[0].start_day == date(2026, 5, 2)
        assert result[0].location == "Field A"

    async def test_get_calendar_skips_unparseable_items(self, client, mocked):
        # Item with malformed day still returns empty list cleanly.
        mocked.get(
            _api("/calendar/2026/5"),
            payload={
                "items": {"a1": {"id": "a1", "day": "garbage", "description": "x"}}
            },
        )
        result = await client.get_calendar(2026, 5)
        assert result == []

    async def test_create_appointment_finds_id_in_response(self, client, mocked):
        mocked.post(
            _api("/calendar/2026/5"),
            payload={
                "items": {
                    "new-id": {
                        "day": "2026-05-02",
                        "description": "Soccer",
                    }
                }
            },
        )
        appt = CoziAppointment.model_validate(
            {
                "description": "Soccer",
                "day": "2026-05-02",
            }
        )
        result = await client.create_appointment(appt)
        assert result.id == "new-id"

    async def test_create_appointment_raises_when_not_in_response(self, client, mocked):
        """Regression: silent failure used to return appointment with id=None."""
        mocked.post(_api("/calendar/2026/5"), payload={"items": {}})
        appt = CoziAppointment.model_validate(
            {
                "description": "Soccer",
                "day": "2026-05-02",
            }
        )
        with pytest.raises(APIError, match="not found"):
            await client.create_appointment(appt)

    async def test_create_appointment_empty_subject_raises(self, client):
        appt = CoziAppointment.model_validate(
            {
                "description": "  ",
                "day": "2026-05-02",
            }
        )
        with pytest.raises(ValidationError):
            await client.create_appointment(appt)

    async def test_update_appointment_requires_id(self, client):
        appt = CoziAppointment.model_validate(
            {
                "description": "Soccer",
                "day": "2026-05-02",
            }
        )
        with pytest.raises(ValidationError):
            await client.update_appointment(appt)

    async def test_update_appointment(self, client, mocked):
        mocked.post(_api("/calendar/2026/5"), payload={"items": {}})
        appt = CoziAppointment.model_validate(
            {
                "id": "a1",
                "description": "Soccer",
                "day": "2026-05-02",
            }
        )
        result = await client.update_appointment(appt)
        assert result.id == "a1"

    async def test_delete_appointment(self, client, mocked):
        mocked.post(_api("/calendar/2026/5"), payload={"items": {}})
        assert await client.delete_appointment("a1", 2026, 5) is True
