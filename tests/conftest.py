"""
Shared fixtures for the unit suite (aioresponses).

Every request carries ``?apikey=...``, so mocks are registered with ``api(path)``,
a regex that matches the path with any query string, and request bodies are read
back with ``sent(mocked, method, path)``.

aioresponses deep-copies the kwargs it records, so it cannot show that a request
body object (a multipart FormData, say) was rebuilt per attempt; only the real-socket
stub (tests/stub_cozi.py) can, since aiohttp refuses to resend a spent FormData.
"""

import re
from typing import Any, Dict, List
from urllib.parse import unquote
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aioresponses import aioresponses

from cozi_client import CoziClient

TEST_USERNAME = "test@example.com"
TEST_PASSWORD = "hunter2"
TEST_ACCOUNT_ID = "acct-123"
TEST_ACCESS_TOKEN = "token-abc"

API_ROOT = "https://rest.cozi.com/api/ext"
LOGIN_PATH = "/2207/auth/login"
REAUTH_PATH = f"/2111/{TEST_ACCOUNT_ID}/auth/reauth"


def api(path: str, root: str = API_ROOT) -> "re.Pattern[str]":
    """Regex for ``root + path`` with any query string. "{acct}" is the test account."""
    path = path.replace("{acct}", TEST_ACCOUNT_ID)
    return re.compile("^" + re.escape(root + path) + r"(\?.*)?$")


AUTH_URL = api(LOGIN_PATH)


def sent(mocked: aioresponses, method: str, path: str, root: str = API_ROOT) -> List[Dict[str, Any]]:
    """The kwargs (json, headers, params, ...) of every request sent to ``method path``."""
    path = path.replace("{acct}", TEST_ACCOUNT_ID)
    calls: List[Dict[str, Any]] = []
    for (m, url), recorded in mocked.requests.items():
        if m == method.upper() and str(url.with_query(None)) == root + path:
            calls.extend(call.kwargs for call in recorded)
    return calls


def sent_query(mocked: aioresponses, method: str, path: str, root: str = API_ROOT) -> List[Dict[str, str]]:
    """The query string of every request sent to ``method path`` (from the recorded URL)."""
    path = path.replace("{acct}", TEST_ACCOUNT_ID)
    return [
        {k: unquote(v) for k, v in url.query.items()}
        for (m, url), recorded in mocked.requests.items()
        for _ in recorded
        if m == method.upper() and str(url.with_query(None)) == root + path
    ]


@pytest.fixture
def auth_response():
    return {
        "accessToken": TEST_ACCESS_TOKEN,
        "expiresIn": 3600,
        "accountId": TEST_ACCOUNT_ID,
    }


@pytest.fixture
def mocked():
    with aioresponses() as m:
        yield m


@pytest.fixture
def no_sleep():
    """Make retry backoff and the request gate instant."""
    with patch("cozi_client._transport.asyncio.sleep", new=AsyncMock()) as m:
        yield m


@pytest_asyncio.fixture
async def stub():
    """An unstarted real-socket Cozi stand-in (see tests/stub_cozi.py)."""
    from tests.stub_cozi import StubCozi

    s = StubCozi()
    yield s
    await s.stop()


@pytest_asyncio.fixture
async def client(mocked, auth_response):
    """A logged-in CoziClient; the login is already mocked and spent."""
    mocked.post(AUTH_URL, payload=auth_response)
    async with CoziClient(TEST_USERNAME, TEST_PASSWORD, retry_attempts=2) as cozi:
        await cozi.authenticate()
        yield cozi
