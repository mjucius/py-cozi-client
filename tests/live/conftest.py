"""
Live conformance harness: these tests talk to the real Cozi API.

Run with ``pytest -m live`` and COZI_USERNAME / COZI_PASSWORD in the environment
(they are skipped without them). Rules, so the suite is safe on a real family account:

- Calendar writes only go to past sandbox months: SANDBOX (2019-01..03), plus
  SEARCH_MONTH (2022-01), since search does not reach back to 2019.
- Everything a test creates is named with ``subject(domain)``:
  "LIVE_TEST_DELETE_ME:<domain> <8 hex>". Each domain sweeps its own prefix before
  and after its module (``sweep(domain)``), so domains can run in parallel without
  deleting each other's fixtures.
- Assertions are exact. Never log pii, subscription, feed URLs or tokens.
- Sweeps use the raw wire (``_request``), not the client methods under test.
"""

import asyncio
import concurrent.futures
import os
import uuid
from typing import Any, Dict, Iterable, List, Tuple

import pytest
import pytest_asyncio

from cozi_client import CoziClient

PREFIX = "LIVE_TEST_DELETE_ME"
SANDBOX: List[Tuple[int, int]] = [(2019, 1), (2019, 2), (2019, 3)]
SEARCH_MONTH: Tuple[int, int] = (2022, 1)

USERNAME = os.environ.get("COZI_USERNAME")
PASSWORD = os.environ.get("COZI_PASSWORD")
HAVE_CREDS = bool(USERNAME and PASSWORD)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # Mark everything under tests/live before `-m 'not live'` deselects by marker.
    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.live)
            # aiohttp finishes closing TLS transports after ClientSession.close()
            # returns; the GC then reports them in whichever test is running.
            item.add_marker(pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"))
            item.add_marker(pytest.mark.filterwarnings("ignore:unclosed:ResourceWarning"))


def subject(domain: str) -> str:
    """A unique, sweepable name for something a live test creates."""
    return f"{PREFIX}:{domain} {uuid.uuid4().hex[:8]}"


def owned_by(domain: str, *texts: Any) -> bool:
    tag = f"{PREFIX}:{domain}"
    return any(isinstance(t, str) and tag in t for t in texts)


def require_creds() -> None:
    if not HAVE_CREDS:
        pytest.skip("COZI_USERNAME / COZI_PASSWORD not set")


def make_client() -> CoziClient:
    require_creds()
    return CoziClient(USERNAME, PASSWORD)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def live_client():
    async with make_client() as cozi:
        await cozi.authenticate()
        yield cozi


# ------------------------------------------------------------------ sweeps


async def sweep_calendar(cozi: CoziClient, domain: str, months: Iterable[Tuple[int, int]] = ()) -> int:
    removed = 0
    for year, month in list(months) or SANDBOX + [SEARCH_MONTH]:
        page = await cozi._request("GET", f"/2004/{{acct}}/calendar/{year}/{month}")
        items: Dict[str, Any] = page.get("items", {}) if isinstance(page, dict) else {}
        for key, item in items.items():
            if not isinstance(item, dict):
                continue
            details = item.get("itemDetails") if isinstance(item.get("itemDetails"), dict) else {}
            meal_texts = [m.get("text") for m in details.get("mealItems", []) if isinstance(m, dict)]
            if owned_by(
                domain,
                item.get("description"),
                item.get("descriptionShort"),
                details.get("name"),
                *meal_texts,
            ):
                await cozi._request(
                    "POST",
                    f"/2004/{{acct}}/calendar/{year}/{month}",
                    json=[{"itemType": "appointment", "delete": {"id": item.get("id") or key}}],
                )
                removed += 1
    return removed


async def sweep_lists(cozi: CoziClient, domain: str) -> int:
    lists = await cozi._request("GET", "/2004/{acct}/list/")
    removed = 0
    for lst in lists if isinstance(lists, list) else []:
        if isinstance(lst, dict) and owned_by(domain, lst.get("title")):
            await cozi._request("DELETE", f"/2004/{{acct}}/list/{lst['listId']}")
            removed += 1
    return removed


async def sweep_recipes(cozi: CoziClient, domain: str) -> int:
    recipes = await cozi._request("GET", "/2103/{acct}/food/recipe/")
    removed = 0
    for recipe in recipes if isinstance(recipes, list) else []:
        if isinstance(recipe, dict) and owned_by(domain, recipe.get("name"), recipe.get("title")):
            await cozi._request("DELETE", f"/2103/{{acct}}/food/recipe/{recipe['recipeBoxId']}")
            removed += 1
    return removed


async def sweep_persons(cozi: CoziClient, domain: str) -> int:
    persons = await cozi._request("GET", "/2004/{acct}/account/person/")
    removed = 0
    for person in persons if isinstance(persons, list) else []:
        if isinstance(person, dict) and owned_by(domain, person.get("name")):
            await cozi._request("DELETE", f"/2004/{{acct}}/account/person/{person['accountPersonId']}")
            removed += 1
    return removed


async def sweep_all(cozi: CoziClient, domain: str) -> int:
    return (
        await sweep_calendar(cozi, domain)
        + await sweep_lists(cozi, domain)
        + await sweep_recipes(cozi, domain)
        + await sweep_persons(cozi, domain)
    )


def run_sync(coro_factory) -> Any:
    """Run a coroutine on its own loop in a worker thread (safe from sync fixtures)."""

    def go():
        return asyncio.run(coro_factory())

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(go).result()


def sweep(domain: str) -> int:
    """Delete everything named for ``domain`` from calendar sandbox, lists, recipes, persons."""
    require_creds()

    async def go():
        async with make_client() as cozi:
            return await sweep_all(cozi, domain)

    return run_sync(go)


def domain_sweeper(domain: str):
    """A module-scoped autouse fixture that sweeps ``domain`` before and after the module."""

    @pytest.fixture(scope="module", autouse=True)
    def _sweep_domain():
        sweep(domain)
        yield
        leftover = sweep(domain)
        assert leftover == 0, f"{leftover} {domain} test subject(s) were left behind and swept"

    return _sweep_domain
