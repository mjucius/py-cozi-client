"""Live: login, cookie token refresh, logout, and the apikey on resource calls."""

import aiohttp
from yarl import URL

from cozi_client import ID_PATTERN, CoziClient
from tests.live.conftest import make_client


async def test_login_and_apikey_on_resource_calls(live_client):
    assert ID_PATTERN.fullmatch(live_client.account_id)
    persons = await live_client._request("GET", "/2004/{acct}/account/person/")
    assert isinstance(persons, list) and persons


async def test_cookie_refresh_works_without_the_password(live_client):
    generation = live_client._token_generation
    # A password login would now fail, so success proves the cookie refresh. (Cozi
    # hands back the same access token while it is still valid: observed 2026-10-16.)
    live_client.password = "definitely-not-the-password"
    live_client._issued_at -= live_client._expires_in or 3600
    persons = await live_client._request("GET", "/2004/{acct}/account/person/")
    assert isinstance(persons, list)
    assert live_client._token_generation == generation + 1


async def test_rejected_token_mid_session_is_renewed(live_client):
    live_client._access_token = "not-a-real-token"
    persons = await live_client._request("GET", "/2004/{acct}/account/person/")
    assert isinstance(persons, list)


async def test_logout_revokes_the_refresh_cookie_on_the_server():
    async with make_client() as cozi:
        await cozi.authenticate()
        account = cozi.account_id
        jar = cozi._session.cookie_jar.filter_cookies(URL(f"{cozi.BASE_URL}/api/ext/2111/{account}/auth/reauth"))
        assert "cozi_refresh" in jar
        refresh = jar["cozi_refresh"].value
        await cozi.logout()
        assert cozi.account_id is None

    # Present the captured cookie directly: clearing the jar alone would prove nothing.
    async with aiohttp.ClientSession() as raw:
        async with raw.post(
            f"{CoziClient.BASE_URL}/api/ext/2111/{account}/auth/reauth",
            params={"apikey": CoziClient.APIKEY},
            headers={
                **CoziClient.BROWSER_HEADERS,
                "Content-Type": "application/json; charset=utf-8",
                "Cookie": f"cozi_refresh={refresh}",
            },
            data=b"",
        ) as response:
            body = await response.json(content_type=None) if await response.read() else None
            status = response.status
    assert not (status == 200 and isinstance(body, dict) and body.get("accessToken")), (
        f"reauth still issued a token after logout (status {status})"
    )
