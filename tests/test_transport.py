"""Transport and auth behaviour, with aioresponses (see tests/test_contract_auth.py for a real socket)."""

import asyncio
import logging
import time

import aiohttp
import pytest
from pydantic import BaseModel

from cozi_client import CoziClient
from cozi_client._transport import parse_model, parse_models, validate_calendar_period, validate_id
from cozi_client.exceptions import (
    APIError,
    AuthenticationError,
    ConflictError,
    CoziException,
    CoziValidationError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    ResourceNotFoundError,
    ResponseParseError,
)
from tests.conftest import (
    AUTH_URL,
    LOGIN_PATH,
    REAUTH_PATH,
    TEST_ACCESS_TOKEN,
    TEST_ACCOUNT_ID,
    TEST_PASSWORD,
    TEST_USERNAME,
    api,
    sent,
    sent_query,
)

LISTS = "/2004/{acct}/list/"
LISTS_URL = api(LISTS)
REAUTH_URL = api(REAUTH_PATH)
LOGOUT_PATH = "/2111/{acct}/auth/logout"


def fresh():
    return CoziClient(TEST_USERNAME, TEST_PASSWORD, retry_attempts=3)


def token(n):
    return {"accessToken": f"tok-{n}", "accountId": TEST_ACCOUNT_ID, "expiresIn": 3600}


class TestRequestShape:
    async def test_apikey_on_login_and_resource_requests(self, client, mocked):
        mocked.get(LISTS_URL, payload=[])
        await client._request("GET", LISTS)
        login_q = sent_query(mocked, "POST", LOGIN_PATH)[0]
        list_q = sent_query(mocked, "GET", LISTS)[0]
        assert login_q["apikey"] == list_q["apikey"] == CoziClient.APIKEY
        assert CoziClient.APIKEY == "coziwc|v265_production"

    async def test_login_body_and_headers(self, client, mocked):
        call = sent(mocked, "POST", LOGIN_PATH)[0]
        assert call["json"] == {
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "issueRefresh": True,
        }
        assert "Authorization" not in call["headers"]
        assert call["headers"]["Origin"] == "https://my.cozi.com"

    async def test_resource_request_carries_bearer_and_browser_headers(self, client, mocked):
        mocked.get(LISTS_URL, payload=[])
        await client._request("GET", LISTS)
        headers = sent(mocked, "GET", LISTS)[0]["headers"]
        assert headers["Authorization"] == f"Bearer {TEST_ACCESS_TOKEN}"
        assert headers["Referer"] == "https://my.cozi.com/"
        assert "Chrome" in headers["User-Agent"]
        assert "Content-Type" not in headers  # aiohttp sets it from json=/FormData

    async def test_timeout_applies_to_injected_session(self, mocked, auth_response):
        mocked.post(AUTH_URL, payload=auth_response)
        async with aiohttp.ClientSession() as session:
            cozi = CoziClient(TEST_USERNAME, TEST_PASSWORD, session=session, request_timeout=7)
            await cozi.authenticate()
        assert sent(mocked, "POST", LOGIN_PATH)[0]["timeout"].total == 7

    async def test_account_id_in_path(self, client, mocked):
        mocked.get(LISTS_URL, payload=[])
        await client._request("GET", LISTS)
        assert len(sent(mocked, "GET", f"/2004/{TEST_ACCOUNT_ID}/list/")) == 1

    async def test_unversioned_base(self, client, mocked):
        path = "/calendar/sharedcalendar/{acct}/"
        mocked.get(api(path, root="https://rest.cozi.com/api"), payload=[])
        assert await client._request("GET", path, unversioned=True) == []


class TestBodies:
    async def test_empty_200_is_true(self, client, mocked):
        mocked.get(LISTS_URL, body=b"")
        assert await client._request("GET", LISTS) is True

    async def test_204_is_true(self, client, mocked):
        mocked.delete(api("/2004/{acct}/list/l1"), status=204)
        assert await client._request("DELETE", "/2004/{acct}/list/l1") is True

    async def test_non_json_200_raises_parse_error_without_retry(self, client, mocked, no_sleep):
        mocked.get(LISTS_URL, body=b"<html>oops</html>", content_type="text/html")
        mocked.get(LISTS_URL, payload=[])
        with pytest.raises(ResponseParseError):
            await client._request("GET", LISTS)
        assert len(sent(mocked, "GET", LISTS)) == 1

    async def test_raw_returns_bytes(self, client, mocked):
        mocked.get(LISTS_URL, body=b"%PDF-1.4")
        assert await client._request("GET", LISTS, raw=True) == b"%PDF-1.4"


class TestErrorMapping:
    @pytest.mark.parametrize(
        "status,exc",
        [
            (400, APIError),
            (402, PermissionDeniedError),
            (403, PermissionDeniedError),
            (404, ResourceNotFoundError),
            (409, ConflictError),
            (410, ConflictError),
        ],
    )
    async def test_status_maps_to_exception(self, client, mocked, status, exc):
        mocked.post(LISTS_URL, status=status, payload={"error": "nope"})
        with pytest.raises(exc) as info:
            await client._request("POST", LISTS, json={})
        assert info.value.status_code == status
        assert "nope" in str(info.value)

    async def test_permission_denied_names_paid_plan(self, client, mocked):
        mocked.get(LISTS_URL, status=403)
        with pytest.raises(PermissionDeniedError, match="paid Cozi plan"):
            await client._request("GET", LISTS)

    async def test_error_without_json_body(self, client, mocked):
        mocked.post(LISTS_URL, status=400, body=b"bad", content_type="text/plain")
        with pytest.raises(APIError) as info:
            await client._request("POST", LISTS, json={})
        assert info.value.response_data == {"error": "No JSON content in error response"}

    async def test_429_retried_then_raises(self, client, mocked, no_sleep):
        mocked.post(LISTS_URL, status=429)
        mocked.post(LISTS_URL, status=429)
        with pytest.raises(RateLimitError):
            await client._request("POST", LISTS, json={})

    async def test_429_retried_for_post(self, client, mocked, no_sleep):
        mocked.post(LISTS_URL, status=429)
        mocked.post(LISTS_URL, payload={"ok": 1})
        assert await client._request("POST", LISTS, json={}) == {"ok": 1}

    async def test_get_500_retried(self, client, mocked, no_sleep):
        mocked.get(LISTS_URL, status=503)
        mocked.get(LISTS_URL, payload=[1])
        assert await client._request("GET", LISTS) == [1]

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
    async def test_writes_not_retried_on_500(self, client, mocked, no_sleep, method):
        mocked.add(LISTS_URL, method, status=500)
        mocked.add(LISTS_URL, method, payload={})
        with pytest.raises(APIError):
            await client._request(method, LISTS, json={})
        assert len(sent(mocked, method, LISTS)) == 1

    async def test_delete_404_after_500_is_success(self, client, mocked, no_sleep):
        url = api("/2004/{acct}/list/l1")
        mocked.delete(url, status=500)
        mocked.delete(url, status=404)
        assert await client._request("DELETE", "/2004/{acct}/list/l1") is True

    async def test_delete_404_is_not_found(self, client, mocked):
        mocked.delete(api("/2004/{acct}/list/l1"), status=404)
        with pytest.raises(ResourceNotFoundError):
            await client._request("DELETE", "/2004/{acct}/list/l1")

    async def test_delete_404_after_network_error_is_not_found(self, client, mocked, no_sleep):
        url = api("/2004/{acct}/list/l1")
        mocked.delete(url, exception=aiohttp.ClientConnectionError("down"))
        mocked.delete(url, status=404)
        with pytest.raises(ResourceNotFoundError):
            await client._request("DELETE", "/2004/{acct}/list/l1")


class TestNetworkErrors:
    async def test_get_timeout_retried(self, client, mocked, no_sleep):
        mocked.get(LISTS_URL, exception=asyncio.TimeoutError())
        mocked.get(LISTS_URL, payload=[])
        assert await client._request("GET", LISTS) == []

    async def test_timeout_is_network_error(self, client, mocked, no_sleep):
        mocked.get(LISTS_URL, exception=asyncio.TimeoutError())
        mocked.get(LISTS_URL, exception=asyncio.TimeoutError())
        with pytest.raises(NetworkError):
            await client._request("GET", LISTS)

    async def test_post_network_error_not_retried(self, client, mocked, no_sleep):
        mocked.post(LISTS_URL, exception=aiohttp.ClientConnectionError("reset"))
        mocked.post(LISTS_URL, payload={})
        with pytest.raises(NetworkError):
            await client._request("POST", LISTS, json={})
        assert len(sent(mocked, "POST", LISTS)) == 1


class TestLogin:
    async def test_lazy_login_on_first_request(self, mocked, auth_response):
        mocked.post(AUTH_URL, payload=auth_response)
        mocked.get(LISTS_URL, payload=[])
        async with fresh() as cozi:
            assert await cozi._request("GET", LISTS) == []
            assert cozi.account_id == TEST_ACCOUNT_ID

    async def test_login_refused_carries_reason(self, mocked):
        mocked.post(AUTH_URL, status=401, payload={"error": "Invalid username or password"})
        async with fresh() as cozi:
            with pytest.raises(AuthenticationError, match="Invalid username or password"):
                await cozi.authenticate()

    async def test_error_key_in_200_login_is_refusal(self, mocked):
        mocked.post(AUTH_URL, payload={"error": "locked"})
        async with fresh() as cozi:
            with pytest.raises(AuthenticationError, match="locked"):
                await cozi.authenticate()

    async def test_missing_fields_named_without_echoing_token(self, mocked):
        mocked.post(AUTH_URL, payload={"accessToken": "secret-token-xyz"})
        async with fresh() as cozi:
            with pytest.raises(AuthenticationError) as info:
                await cozi.authenticate()
        assert "accountId" in str(info.value)
        assert "secret-token-xyz" not in str(info.value)

    async def test_non_object_login_response(self, mocked):
        mocked.post(AUTH_URL, payload=["x"])
        async with fresh() as cozi:
            with pytest.raises(AuthenticationError, match="expected an object"):
                await cozi.authenticate()

    async def test_no_token_or_password_in_logs(self, mocked, auth_response, caplog):
        caplog.set_level(logging.DEBUG, logger="cozi_client")
        mocked.post(AUTH_URL, payload=auth_response)
        mocked.get(LISTS_URL, payload=[])
        async with fresh() as cozi:
            await cozi._request("GET", LISTS)
        assert TEST_ACCESS_TOKEN not in caplog.text
        assert TEST_PASSWORD not in caplog.text

    async def test_concurrent_first_calls_log_in_once(self, mocked, auth_response):
        mocked.post(AUTH_URL, payload=auth_response)
        for _ in range(5):
            mocked.get(LISTS_URL, payload=[])
        async with fresh() as cozi:
            await asyncio.gather(*(cozi._request("GET", LISTS) for _ in range(5)))
        assert len(sent(mocked, "POST", LOGIN_PATH)) == 1


class TestRenewal:
    async def test_401_renews_by_cookie_and_replays(self, client, mocked):
        mocked.get(LISTS_URL, status=401)
        mocked.post(REAUTH_URL, payload=token(2))
        mocked.get(LISTS_URL, payload=["ok"])
        assert await client._request("GET", LISTS) == ["ok"]
        reauth = sent(mocked, "POST", REAUTH_PATH)[0]
        assert reauth["data"] == b""
        assert "Authorization" not in reauth["headers"]
        assert reauth["headers"]["Content-Type"].startswith("application/json")
        assert sent(mocked, "GET", LISTS)[1]["headers"]["Authorization"] == "Bearer tok-2"
        assert len(sent(mocked, "POST", LOGIN_PATH)) == 1  # only the fixture's

    async def test_refused_cookie_falls_back_to_password(self, client, mocked, auth_response):
        mocked.get(LISTS_URL, status=401)
        mocked.post(REAUTH_URL, status=401)
        mocked.post(AUTH_URL, payload=auth_response)
        mocked.get(LISTS_URL, payload=["ok"])
        assert await client._request("GET", LISTS) == ["ok"]
        assert len(sent(mocked, "POST", LOGIN_PATH)) == 2

    async def test_401_replay_does_not_use_a_retry_attempt(self, mocked, auth_response):
        mocked.post(AUTH_URL, payload=auth_response)
        mocked.get(LISTS_URL, status=401)
        mocked.post(REAUTH_URL, payload=token(2))
        mocked.get(LISTS_URL, payload=["ok"])
        async with CoziClient(TEST_USERNAME, TEST_PASSWORD, retry_attempts=1) as cozi:
            assert await cozi._request("GET", LISTS) == ["ok"]

    async def test_second_401_raises(self, client, mocked):
        mocked.get(LISTS_URL, status=401)
        mocked.post(REAUTH_URL, payload=token(2))
        mocked.get(LISTS_URL, status=401)
        with pytest.raises(AuthenticationError):
            await client._request("GET", LISTS)

    async def test_concurrent_401s_renew_once(self, client, mocked):
        for _ in range(3):
            mocked.get(LISTS_URL, status=401)
        mocked.post(REAUTH_URL, payload=token(2))
        for _ in range(3):
            mocked.get(LISTS_URL, payload=[])
        await asyncio.gather(*(client._request("GET", LISTS) for _ in range(3)))
        assert len(sent(mocked, "POST", REAUTH_PATH)) == 1

    async def test_expiring_token_renewed_before_the_request(self, mocked):
        mocked.post(AUTH_URL, payload={**token(1), "expiresIn": 100})  # inside the 180 s margin
        mocked.post(REAUTH_URL, payload=token(2))
        mocked.get(LISTS_URL, payload=[])
        async with fresh() as cozi:
            await cozi.authenticate()
            await cozi._request("GET", LISTS)
        assert sent(mocked, "GET", LISTS)[0]["headers"]["Authorization"] == "Bearer tok-2"

    async def test_fresh_token_not_renewed(self, client, mocked):
        mocked.get(LISTS_URL, payload=[])
        await client._request("GET", LISTS)
        assert sent(mocked, "POST", REAUTH_PATH) == []

    async def test_token_renewed_once_its_life_is_nearly_up(self, client, mocked):
        # Age the token rather than patching time.monotonic, which is the event loop's clock too.
        client._issued_at -= 3600 - 179
        mocked.post(REAUTH_URL, payload=token(2))
        mocked.get(LISTS_URL, payload=[])
        await client._request("GET", LISTS)
        assert len(sent(mocked, "POST", REAUTH_PATH)) == 1


class TestLogout:
    async def test_logout_ends_server_session_and_local_state(self, client, mocked, auth_response):
        mocked.post(api(LOGOUT_PATH), status=200)
        await client.logout()
        call = sent(mocked, "POST", LOGOUT_PATH)[0]
        assert "Authorization" not in call["headers"]
        assert call["data"] == b""
        assert client.account_id is None
        mocked.post(AUTH_URL, payload=auth_response)
        mocked.get(LISTS_URL, payload=[])
        await client._request("GET", LISTS)
        assert len(sent(mocked, "POST", LOGIN_PATH)) == 2

    async def test_logout_ignores_server_errors(self, client, mocked):
        mocked.post(api(LOGOUT_PATH), status=500)
        await client.logout()
        assert client.account_id is None

    async def test_logout_before_login_sends_nothing(self, mocked):
        async with fresh() as cozi:
            await cozi.logout()
        assert mocked.requests == {}


class TestPasswords:
    async def test_change_password_adopts_new_token(self, client, mocked):
        mocked.post(api("/2111/{acct}/auth/password"), payload={"accessToken": "tok-new", "expiresIn": 3600})
        mocked.get(LISTS_URL, payload=[])
        await client.change_password("n3w")
        assert sent(mocked, "POST", "/2111/{acct}/auth/password")[0]["json"] == {"newPassword": "n3w"}
        await client._request("GET", LISTS)
        assert sent(mocked, "GET", LISTS)[0]["headers"]["Authorization"] == "Bearer tok-new"
        assert client.password == "n3w"
        assert client.account_id == TEST_ACCOUNT_ID

    async def test_change_password_rejects_empty(self, client, mocked):
        with pytest.raises(CoziValidationError):
            await client.change_password("")

    async def test_request_password_reset_needs_no_login(self, mocked):
        path = "/2110/household/password/resetrequest"
        mocked.post(api(path), status=200)
        async with fresh() as cozi:
            await cozi.request_password_reset(" me@example.com ")
        call = sent(mocked, "POST", path)[0]
        assert call["json"] == {"email": "me@example.com"}
        assert "Authorization" not in call["headers"]
        assert sent(mocked, "POST", LOGIN_PATH) == []

    async def test_submit_password_reset(self, mocked):
        path = "/2110/acct-9/household/password/resetrequest/req-1"
        mocked.put(api(path), status=200)
        async with fresh() as cozi:
            await cozi.submit_password_reset("acct-9", "req-1", "n3w")
        assert sent(mocked, "PUT", path)[0]["json"] == {"newPassword": "n3w"}

    async def test_submit_password_reset_validates_ids(self, mocked):
        async with fresh() as cozi:
            with pytest.raises(CoziValidationError):
                await cozi.submit_password_reset("../x", "req-1", "n3w")


class TestGate:
    async def test_concurrent_requests_are_spaced(self, client, mocked, monkeypatch):
        monkeypatch.setattr(client, "MIN_REQUEST_INTERVAL", 0.05)
        for _ in range(3):
            mocked.get(LISTS_URL, payload=[])
        start = time.monotonic()
        await asyncio.gather(*(client._request("GET", LISTS) for _ in range(3)))
        assert time.monotonic() - start >= 0.1


class TestValidators:
    @pytest.mark.parametrize("good", ["abc", "A-b_9", "0ff00c00f4106e69fe473d8941238e971025b7cb"])
    def test_validate_id_accepts(self, good):
        assert validate_id(good) == good

    @pytest.mark.parametrize("bad", ["", "../x", "a/b", "a?b", "a#b", "a~b", "id\n", None, 5])
    def test_validate_id_rejects(self, bad):
        with pytest.raises(CoziValidationError):
            validate_id(bad, "list_id")

    def test_validate_calendar_period(self):
        assert validate_calendar_period(2024, 12) == (2024, 12)

    @pytest.mark.parametrize("year,month", [(2024, 0), (2024, 13), (999, 1), (10000, 1), ("2024", 1), (2024, True)])
    def test_validate_calendar_period_rejects(self, year, month):
        with pytest.raises(CoziValidationError):
            validate_calendar_period(year, month)


class _Thing(BaseModel):
    n: int


class TestParseHelpers:
    def test_parse_model_wraps_pydantic_errors(self):
        with pytest.raises(ResponseParseError) as info:
            parse_model(_Thing, {"n": "x"})
        assert isinstance(info.value, CoziException)

    def test_parse_models_needs_a_list(self):
        with pytest.raises(ResponseParseError):
            parse_models(_Thing, {"n": 1})
        assert parse_models(_Thing, [{"n": 1}]) == [_Thing(n=1)]
