"""Live: login, cookie token refresh, logout, and the apikey on resource calls."""

from http.cookies import SimpleCookie
from typing import Dict, Tuple

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


async def raw_reauth(account: str, cookies: Dict[str, str]) -> Tuple[int, bool, SimpleCookie]:
    """
    POST reauth from a fresh session that carries only ``cookies``.

    Returns the status, whether a token was issued (never the token itself) and the
    response's Set-Cookie. Keep the body out of every assert: on success it holds a token.
    """
    async with aiohttp.ClientSession() as raw:
        async with raw.post(
            f"{CoziClient.BASE_URL}/api/ext/2111/{account}/auth/reauth",
            params={"apikey": CoziClient.APIKEY},
            headers={
                **CoziClient.BROWSER_HEADERS,
                "Content-Type": "application/json; charset=utf-8",
                "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
            },
            data=b"",
        ) as response:
            body = await response.json(content_type=None) if await response.read() else None
            issued = response.status == 200 and isinstance(body, dict) and bool(body.get("accessToken"))
            return response.status, issued, response.cookies


async def test_logout_revokes_the_refresh_cookie_on_the_server():
    async with make_client() as cozi:
        await cozi.authenticate()
        account = cozi.account_id
        reauth_url = URL(f"{cozi.BASE_URL}/api/ext/2111/{account}/auth/reauth")
        # Replay everything the jar sends: Cozi refuses cozi_refresh without device_id
        # (401, observed 2026-10-16).
        cookies = {k: m.value for k, m in cozi._session.cookie_jar.filter_cookies(reauth_url).items()}
        has_refresh = "cozi_refresh" in cookies
        assert has_refresh, "login set no cozi_refresh cookie"

        # Control: the same raw request is honoured while the cookie is live, so a
        # refusal after logout is the server revoking it, not a malformed request.
        status, issued, set_cookies = await raw_reauth(account, cookies)
        assert issued, f"control reauth refused a live cookie (status {status})"
        # Reauth rotates the cookie (observed 2026-10-16). Hand the new one to the client
        # so logout revokes the cookie under test.
        rotated = set_cookies.get("cozi_refresh")
        if rotated is not None and rotated.value:
            cookies["cozi_refresh"] = rotated.value
            cozi._session.cookie_jar.update_cookies(set_cookies, reauth_url)

        await cozi.logout()
        assert cozi.account_id is None

    status, issued, _ = await raw_reauth(account, cookies)
    assert not issued, f"reauth still issued a token after logout (status {status})"
    assert status == 401, f"expected 401 for a revoked cookie, got {status}"
