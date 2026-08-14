"""
End-to-end tests over real HTTP, with no mocking library involved.

Every other client test goes through ``aioresponses``, which builds aiohttp's
``ClientResponse`` by hand. That makes the whole suite blind in one specific way:
when aiohttp 3.14 made ``stream_writer`` a required argument of
``ClientResponse.__init__``, aioresponses had not caught up, and 63 tests failed
before reaching a single line of client code — while the client itself was
perfectly fine against a real server.

These tests talk to an actual aiohttp server on a real socket, so they exercise
the client the way a user does and stay honest across aiohttp releases. They are
what the aiohttp-latest CI job runs.
"""

import pytest
from aiohttp import web

from cozi_client import CoziClient
from exceptions import ResourceNotFoundError, ValidationError

ACCOUNT = "acct-1"


def _build_app() -> web.Application:
    """A minimal stand-in for the handful of Cozi endpoints used below."""

    async def login(request):
        return web.json_response(
            {"accessToken": "tok", "accountId": ACCOUNT, "expiresIn": 3600}
        )

    async def get_lists(request):
        return web.json_response(
            [{"listId": "l1", "title": "Groceries", "listType": "shopping", "items": []}]
        )

    async def add_item(request):
        body = await request.json()
        return web.json_response(
            {"itemId": "i1", "text": body["text"], "status": "incomplete"}
        )

    async def put_item(request):
        body = await request.json()
        return web.json_response(
            {
                "itemId": request.match_info["item_id"],
                "text": body.get("text", "unchanged"),
                "status": body.get("status", "incomplete"),
            }
        )

    async def missing(request):
        return web.json_response({"error": "no such list"}, status=404)

    app = web.Application()
    base = f"/api/ext/2004/{ACCOUNT}"
    app.router.add_post("/api/ext/2207/auth/login", login)
    app.router.add_get(f"{base}/list/", get_lists)
    app.router.add_post(f"{base}/list/l1/item/", add_item)
    app.router.add_put(f"{base}/list/l1/item/{{item_id}}", put_item)
    app.router.add_delete(f"{base}/list/nope", missing)
    return app


@pytest.fixture
async def server_url():
    """Run the stub app on an ephemeral port and yield its base URL."""
    runner = web.AppRunner(_build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.fixture
async def live_client(server_url, monkeypatch):
    monkeypatch.setattr(CoziClient, "BASE_URL", server_url)
    async with CoziClient("user", "pass") as client:
        yield client


class TestAgainstRealServer:
    async def test_authenticate(self, live_client):
        await live_client.authenticate()
        assert live_client._authenticated is True
        assert live_client._account_id == ACCOUNT

    async def test_get_lists(self, live_client):
        lists = await live_client.get_lists()
        assert [l.title for l in lists] == ["Groceries"]

    async def test_add_item(self, live_client):
        item = await live_client.add_item("l1", "Milk")
        assert (item.id, item.text) == ("i1", "Milk")

    async def test_update_item_text(self, live_client):
        item = await live_client.update_item_text("l1", "i1", "Bread")
        assert item.text == "Bread"

    async def test_404_maps_to_resource_not_found(self, live_client):
        with pytest.raises(ResourceNotFoundError):
            await live_client.delete_list("nope")

    async def test_traversal_id_never_reaches_the_socket(self, live_client):
        # The server has no matching route, so a leak would surface as a 404
        # (ResourceNotFoundError), not a ValidationError.
        with pytest.raises(ValidationError):
            await live_client.delete_list("../../evil")
