"""ListsMixin over a real socket, against tests/stub_cozi.py (strict paths, apikey, bearer)."""

import pytest
from aiohttp import web

from cozi_client.exceptions import ResourceNotFoundError
from cozi_client.lists import CoziItem, ItemStatus, ListType
from tests.conftest import L1, LISTS
from tests.test_models_lists import RAW

LIST = {**RAW, "items": []}
ITEM = {"itemId": "i1", "text": "Milk", "itemType": None, "status": "incomplete"}


def wire(stub):
    """(method, path, query without apikey, json) of every request after the login."""
    return [
        (r["method"], r["path"], {k: v for k, v in r["query"].items() if k != "apikey"}, r["json"])
        for r in stub.requests
        if not r["path"].endswith("/auth/login")
    ]


async def test_every_endpoint_at_its_exact_path(stub):
    async def get_lists(request: web.Request) -> web.Response:
        wanted = request.query.get("list_type")
        return web.json_response([lst for lst in [RAW] if wanted in (None, lst["listType"])])

    stub.route("GET", LISTS, get_lists)
    stub.route("POST", LISTS, json=LIST, status=201)
    stub.route("PATCH", LISTS, json=[LIST])
    stub.route("PUT", L1, json=RAW)
    stub.route("DELETE", L1)
    stub.route("PATCH", L1, json=LIST)
    stub.route("POST", L1 + "/item/", json={**ITEM, "text": "PRODUCE", "itemType": "header"}, status=201)
    stub.route("PUT", L1 + "/item/i1", json={**ITEM, "status": "complete"})
    stub.route("DELETE", L1 + "/item/i1")

    async with stub.client() as cozi:
        assert [lst.id for lst in await cozi.get_lists(ListType.SHOPPING)] == ["L1"]
        assert await cozi.get_lists("todo") == []
        await cozi.create_list("Groceries", ListType.SHOPPING)
        lst = await cozi.update_list(
            (await cozi.get_lists())[0].model_copy(update={"items": [CoziItem(text="Eggs")]})
        )
        assert lst.version == RAW["version"]
        assert [x.id for x in await cozi.reorder_list("L1", 0)] == ["L1"]
        assert (await cozi.add_item("L1", "produce", header=True)).is_header
        assert (await cozi.mark_item("L1", "i1", ItemStatus.COMPLETE)).status is ItemStatus.COMPLETE
        await cozi.delete_item("L1", "i1")
        await cozi.remove_items("L1", ["i2"])
        await cozi.delete_list("L1")

    acct = stub.ACCOUNT
    base = f"/api/ext/2004/{acct}/list/"
    put_body = {**RAW, "items": [{"text": "Eggs"}]}
    assert wire(stub) == [
        ("GET", base, {"list_type": "shopping"}, None),
        ("GET", base, {"list_type": "todo"}, None),
        ("POST", base, {}, {"title": "Groceries", "listType": "shopping"}),
        ("GET", base, {}, None),
        ("PUT", base + "L1", {}, put_body),
        ("PATCH", base, {}, {"operations": [{"op": "move", "from": "L1", "path": "0"}]}),
        ("POST", base + "L1/item/", {}, {"text": "PRODUCE", "position": 0, "itemType": "header"}),
        ("PUT", base + "L1/item/i1", {}, {"status": "complete"}),
        ("DELETE", base + "L1/item/i1", {}, None),
        ("PATCH", base + "L1", {}, {"operations": [{"op": "remove", "path": "/items/i2"}]}),
        ("DELETE", base + "L1", {}, None),
    ]
    assert all(r["headers"]["Authorization"] == "Bearer tok-1" for r in stub.requests[1:])


@pytest.mark.parametrize(
    "call, cleanup",
    [
        (lambda c: c.update_item_text("L1", "i1", "ghost"), ("PATCH", L1)),
        (lambda c: c.rename_list("L1", "ghost"), ("DELETE", L1)),
    ],
)
async def test_201_upsert_is_undone_and_refused(stub, call, cleanup):
    stub.route("PUT", L1 + "/item/i1", json={**ITEM, "text": "ghost"}, status=201)
    stub.route("PUT", L1, json={**LIST, "title": "ghost"}, status=201)
    stub.route("PATCH", L1, json=LIST)
    stub.route("DELETE", L1)
    async with stub.client() as cozi:
        with pytest.raises(ResourceNotFoundError, match="was removed"):
            await call(cozi)
    assert len(stub.sent(*cleanup)) == 1
