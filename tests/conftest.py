"""Shared pytest fixtures for the py-cozi-client test suite."""

import re

import pytest
import pytest_asyncio
from aioresponses import aioresponses

from cozi_client import CoziClient

TEST_USERNAME = "test@example.com"
TEST_PASSWORD = "hunter2"
TEST_ACCOUNT_ID = "acct-123"
TEST_ACCESS_TOKEN = "token-abc"

AUTH_URL = re.compile(r"https://rest\.cozi\.com/api/ext/\d+/auth/login")


@pytest.fixture
def auth_response():
    return {
        "accessToken": TEST_ACCESS_TOKEN,
        "expiresIn": 3600,
        "accountId": TEST_ACCOUNT_ID,
    }


@pytest.fixture
def mocked():
    """aioresponses context manager for mocking aiohttp calls."""
    with aioresponses() as m:
        yield m


@pytest_asyncio.fixture
async def client(mocked, auth_response):
    """Authenticated CoziClient. Auth endpoint is pre-mocked."""
    mocked.post(AUTH_URL, payload=auth_response)
    async with CoziClient(TEST_USERNAME, TEST_PASSWORD, retry_attempts=2) as cozi:
        await cozi.authenticate()
        yield cozi
