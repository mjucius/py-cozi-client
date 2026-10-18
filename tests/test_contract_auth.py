"""Auth and transport over a real socket, against tests/stub_cozi.py."""

import asyncio

import pytest

from cozi_client.exceptions import AuthenticationError, CoziValidationError, ResourceNotFoundError
from tests.conftest import LISTS


async def test_login_sets_refresh_cookie_and_every_request_has_apikey(stub):
    stub.route("GET", LISTS, json=[])
    async with stub.client() as cozi:
        await cozi._request("GET", LISTS)
    assert stub.logins == 1
    assert stub.refresh_cookie is not None
    assert all(r["query"]["apikey"] == "coziwc|v265_production" for r in stub.requests)
    assert stub.sent("GET", LISTS)[0]["headers"]["Authorization"] == "Bearer tok-1"


async def test_expiring_token_renewed_with_cookie_not_password(stub):
    stub.expires_in = 100  # inside the 180 s renewal margin
    stub.route("GET", LISTS, json=[])
    async with stub.client() as cozi:
        await cozi.authenticate()
        await cozi._request("GET", LISTS)
    assert (stub.logins, stub.reauths) == (1, 1)
    reauth = stub.sent("POST", "/2111/{acct}/auth/reauth")[0]
    assert reauth["body"] == b""
    assert reauth["content_type"] == "application/json"
    assert "Authorization" not in reauth["headers"]
    assert reauth["cookies"]["cozi_refresh"] == "refresh-1"
    assert stub.sent("GET", LISTS)[0]["headers"]["Authorization"] == "Bearer tok-2"


async def test_401_mid_session_renews_and_replays(stub):
    stub.route("GET", LISTS, json=["ok"])
    async with stub.client() as cozi:
        await cozi.authenticate()
        stub.token = "rotated-elsewhere"  # the server no longer accepts tok-1
        assert await cozi._request("GET", LISTS) == ["ok"]
    assert (stub.logins, stub.reauths) == (1, 1)


async def test_refused_refresh_falls_back_to_password(stub):
    stub.route("GET", LISTS, json=["ok"])
    async with stub.client() as cozi:
        await cozi.authenticate()
        stub.refresh_cookie = "revoked"
        stub.token = "rotated-elsewhere"
        assert await cozi._request("GET", LISTS) == ["ok"]
    assert stub.logins == 2


async def test_concurrent_calls_share_one_login(stub):
    stub.route("GET", LISTS, json=[])
    async with stub.client() as cozi:
        await asyncio.gather(*(cozi._request("GET", LISTS) for _ in range(5)))
    assert stub.logins == 1


async def test_logout_revokes_refresh_and_clears_cookie(stub):
    stub.route("GET", LISTS, json=[])
    async with stub.client() as cozi:
        await cozi.authenticate()
        await cozi.logout()
        logout = stub.sent("POST", "/2111/{acct}/auth/logout")[0]
        assert logout["cookies"]["cozi_refresh"] == "refresh-1"
        assert "Authorization" not in logout["headers"]
        assert stub.refresh_cookie is None
        assert len(cozi._session.cookie_jar) == 0
        await cozi._request("GET", LISTS)  # logs in again
    assert stub.logins == 2


async def test_wrong_password_is_authentication_error(stub):
    async with stub.client() as cozi:
        cozi.password = "wrong"
        with pytest.raises(AuthenticationError, match="Invalid credentials"):
            await cozi.authenticate()


async def test_paths_are_exact(stub):
    stub.route("GET", LISTS, json=[])
    async with stub.client() as cozi:
        with pytest.raises(ResourceNotFoundError):
            await cozi._request("GET", "/2004/{acct}/list")  # no trailing slash


async def test_traversal_id_never_reaches_the_socket(stub):
    async with stub.client() as cozi:
        await cozi.authenticate()
        before = len(stub.requests)
        with pytest.raises(CoziValidationError):
            await cozi.delete_list("../../evil")
        with pytest.raises(CoziValidationError):
            await cozi.add_item("l1?x=1", "Milk")
    assert len(stub.requests) == before
