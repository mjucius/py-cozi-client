"""ListsMixin over aioresponses: exact method, path and body for every endpoint, plus failure modes."""

import copy

import pytest

from cozi_client import CoziClient
from cozi_client.exceptions import (
    APIError,
    ConflictError,
    CoziValidationError,
    ResourceNotFoundError,
    ResponseParseError,
    WriteVerificationError,
)
from cozi_client.lists import CoziItem, CoziList, ItemStatus, ListType
from tests.conftest import api, sent, sent_query
from tests.test_models_lists import RAW

LISTS = "/2004/{acct}/list/"
L1 = LISTS + "L1"
ITEMS = L1 + "/item/"
I1 = ITEMS + "i1"


def item(text="Milk", item_id="i1", **kw):
    return {"itemId": item_id, "text": text, "itemType": None, "status": "incomplete", **kw}


def requests_made(mocked):
    """Every (method, path) sent after the login."""
    return [
        (m, str(url.with_query(None)).split("/api/ext", 1)[1])
        for (m, url), calls in mocked.requests.items()
        for _ in calls
        if not str(url).split("?")[0].endswith("/auth/login")
    ]


class TestGetLists:
    async def test_all_lists(self, client, mocked):
        mocked.get(api(LISTS), payload=[RAW])
        lists = await client.get_lists()
        assert [(lst.id, lst.list_type, [i.is_header for i in lst.items]) for lst in lists] == [
            ("L1", ListType.SHOPPING, [True, False])
        ]
        assert sent_query(mocked, "GET", LISTS) == [{"apikey": CoziClient.APIKEY}]

    @pytest.mark.parametrize("list_type", [ListType.SHOPPING, "shopping"])
    async def test_type_goes_on_the_wire_as_its_value(self, client, mocked, list_type):
        mocked.get(api(LISTS), payload=[RAW])
        await client.get_lists(list_type)
        assert sent_query(mocked, "GET", LISTS) == [{"apikey": CoziClient.APIKEY, "list_type": "shopping"}]

    async def test_bad_type_raises_before_io(self, client, mocked):
        with pytest.raises(CoziValidationError):
            await client.get_lists("wishlist")
        assert requests_made(mocked) == []

    async def test_non_array_body_is_a_parse_error(self, client, mocked):
        mocked.get(api(LISTS), payload={"error": "nope"})
        with pytest.raises(ResponseParseError):
            await client.get_lists()


class TestCreateRenameDelete:
    @pytest.mark.parametrize("list_type", [ListType.TODO, "todo"])
    async def test_create(self, client, mocked, list_type):
        mocked.post(api(LISTS), status=201, payload={**RAW, "listType": "todo", "items": []})
        lst = await client.create_list("Chores", list_type)
        assert sent(mocked, "POST", LISTS)[0]["json"] == {"title": "Chores", "listType": "todo"}
        assert (lst.id, lst.list_type) == ("L1", ListType.TODO)

    @pytest.mark.parametrize("title, list_type", [("  ", "todo"), (None, "todo"), ("x", "wishlist")])
    async def test_create_rejects_before_io(self, client, mocked, title, list_type):
        with pytest.raises(CoziValidationError):
            await client.create_list(title, list_type)
        assert requests_made(mocked) == []

    async def test_rename_sends_only_the_title(self, client, mocked):
        mocked.put(api(L1), payload={**RAW, "title": "New"})
        lst = await client.rename_list("L1", "New")
        assert sent(mocked, "PUT", L1)[0]["json"] == {"title": "New"}
        assert lst.title == "New"

    async def test_rename_not_applied_raises(self, client, mocked):
        mocked.put(api(L1), payload=RAW)
        with pytest.raises(WriteVerificationError):
            await client.rename_list("L1", "New")

    async def test_delete(self, client, mocked):
        mocked.delete(api(L1), status=204)
        assert await client.delete_list("L1") is True
        assert requests_made(mocked) == [("DELETE", "/2004/acct-123/list/L1")]

    async def test_delete_retried_after_5xx_that_finds_nothing_is_success(self, client, mocked, no_sleep):
        mocked.delete(api(L1), status=500)
        mocked.delete(api(L1), status=404)
        assert await client.delete_list("L1") is True
        assert len(sent(mocked, "DELETE", L1)) == 2


class TestUpdateList:
    async def test_body_is_the_list_as_read_with_items_reordered(self, client, mocked):
        lst = CoziList.model_validate(copy.deepcopy(RAW))
        lst.items = list(reversed(lst.items))
        lst.items.append(CoziItem(text="Eggs"))
        response = {**RAW, "version": 42}
        mocked.put(api(L1), payload=response)

        stored = await client.update_list(lst)

        body = sent(mocked, "PUT", L1)[0]["json"]
        assert body == {**RAW, "items": [RAW["items"][1], RAW["items"][0], {"text": "Eggs"}]}
        assert stored.version == 42

    async def test_hand_built_list_sends_only_what_was_set(self, client, mocked):
        mocked.put(api(L1), payload=RAW)
        await client.update_list(CoziList(id="L1", title="T", items=[CoziItem(id="i1", text="x")]))
        assert sent(mocked, "PUT", L1)[0]["json"] == {
            "listId": "L1",
            "title": "T",
            "items": [{"itemId": "i1", "text": "x"}],
        }

    async def test_stale_version_is_a_conflict(self, client, mocked):
        mocked.put(api(L1), status=409, payload={"error": "conflict"})
        with pytest.raises(ConflictError):
            await client.update_list(CoziList.model_validate(RAW))

    async def test_upsert_deletes_the_phantom_list(self, client, mocked):
        mocked.put(api(L1), status=201, payload=RAW)
        mocked.delete(api(L1), status=204)
        with pytest.raises(ResourceNotFoundError, match="was removed") as e:
            await client.update_list(CoziList.model_validate(RAW))
        assert e.value.status_code == 201
        assert requests_made(mocked) == [
            ("PUT", "/2004/acct-123/list/L1"),
            ("DELETE", "/2004/acct-123/list/L1"),
        ]

    async def test_upsert_cleanup_failure_still_raises_not_found(self, client, mocked, no_sleep):
        mocked.put(api(L1), status=201, payload=RAW)
        mocked.delete(api(L1), status=403, repeat=True)
        with pytest.raises(ResourceNotFoundError, match="could NOT be removed"):
            await client.update_list(CoziList.model_validate(RAW))

    @pytest.mark.parametrize("lst", [CoziList(title="no id"), CoziList(id="../x"), RAW])
    async def test_rejects_before_io(self, client, mocked, lst):
        with pytest.raises(CoziValidationError):
            await client.update_list(lst)
        assert requests_made(mocked) == []


class TestReorderList:
    async def test_move_body_and_result(self, client, mocked):
        mocked.patch(api(LISTS), payload=[{**RAW, "listId": "L2"}, RAW])
        lists = await client.reorder_list("L1", 1)
        assert sent(mocked, "PATCH", LISTS)[0]["json"] == {
            "operations": [{"op": "move", "from": "L1", "path": "1"}]
        }
        assert [lst.id for lst in lists] == ["L2", "L1"]

    @pytest.mark.parametrize("index", [-1, True, "1", 1.0, None])
    async def test_bad_index_raises_before_io(self, client, mocked, index):
        with pytest.raises(CoziValidationError):
            await client.reorder_list("L1", index)
        assert requests_made(mocked) == []


class TestAddItem:
    async def test_plain_item(self, client, mocked):
        mocked.post(api(ITEMS), status=201, payload=item("Milk"))
        result = await client.add_item("L1", "Milk")
        assert sent(mocked, "POST", ITEMS)[0]["json"] == {"text": "Milk", "position": 0}
        assert (result.id, result.text, result.is_header) == ("i1", "Milk", False)

    async def test_header_is_upper_cased_and_typed(self, client, mocked):
        mocked.post(api(ITEMS), status=201, payload=item("PRODUCE", itemType="header"))
        result = await client.add_item("L1", "produce", position=2, header=True)
        assert sent(mocked, "POST", ITEMS)[0]["json"] == {
            "text": "PRODUCE",
            "position": 2,
            "itemType": "header",
        }
        assert result.is_header

    async def test_all_caps_plain_item_is_sent_in_sentence_case(self, client, mocked):
        mocked.post(api(ITEMS), status=201, payload=item("Milk"))
        await client.add_item("L1", "MILK")
        assert sent(mocked, "POST", ITEMS)[0]["json"] == {"text": "Milk", "position": 0}

    @pytest.mark.parametrize(
        "response",
        [item("Other"), item("Milk", item_id=None), item("Milk", itemType="header")],
    )
    async def test_not_applied_raises(self, client, mocked, response):
        mocked.post(api(ITEMS), status=201, payload=response)
        with pytest.raises(WriteVerificationError):
            await client.add_item("L1", "Milk")

    async def test_header_that_came_back_plain_raises(self, client, mocked):
        mocked.post(api(ITEMS), status=201, payload=item("PRODUCE"))
        with pytest.raises(WriteVerificationError, match="not a header"):
            await client.add_item("L1", "produce", header=True)

    @pytest.mark.parametrize(
        "list_id, text, position, header",
        [
            ("../x", "Milk", 0, False),
            ("L1", "", 0, False),
            ("L1", None, 0, False),
            ("L1", 5, 0, True),
            ("L1", "Milk", -1, False),
            ("L1", "Milk", True, False),
            ("L1", "2024", 0, True),
            ("L1", "A", 0, False),
            # A non-bool header would send one kind and verify against another:
            # the write lands, then a false WriteVerificationError invites a retry.
            ("L1", "Milk", 0, None),
            ("L1", "Milk", 0, "yes"),
            ("L1", "Milk", 0, 1),
        ],
    )
    async def test_rejects_before_io(self, client, mocked, list_id, text, position, header):
        with pytest.raises(CoziValidationError):
            await client.add_item(list_id, text, position=position, header=header)
        assert requests_made(mocked) == []


class TestItemUpdates:
    async def test_update_text(self, client, mocked):
        mocked.put(api(I1), payload=item("Oat milk"))
        result = await client.update_item_text("L1", "i1", "Oat milk")
        assert sent(mocked, "PUT", I1)[0]["json"] == {"text": "Oat milk"}
        assert result.text == "Oat milk"

    async def test_update_header_text(self, client, mocked):
        mocked.put(api(I1), payload=item("DAIRY", itemType="header"))
        result = await client.update_item_text("L1", "i1", "Dairy", header=True)
        assert sent(mocked, "PUT", I1)[0]["json"] == {"text": "DAIRY"}
        assert result.is_header

    async def test_update_text_not_applied_raises(self, client, mocked):
        mocked.put(api(I1), payload=item("Milk"))
        with pytest.raises(WriteVerificationError):
            await client.update_item_text("L1", "i1", "Oat milk")

    @pytest.mark.parametrize("status", [ItemStatus.COMPLETE, "complete"])
    async def test_mark(self, client, mocked, status):
        mocked.put(api(I1), payload=item(status="complete"))
        result = await client.mark_item("L1", "i1", status)
        assert sent(mocked, "PUT", I1)[0]["json"] == {"status": "complete"}
        assert result.status is ItemStatus.COMPLETE

    async def test_mark_not_applied_raises(self, client, mocked):
        mocked.put(api(I1), payload=item(status="incomplete"))
        with pytest.raises(
            WriteVerificationError, match="asked for 'complete', server returned 'incomplete'"
        ):
            await client.mark_item("L1", "i1", ItemStatus.COMPLETE)

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.mark_item("L1", "i1", "done"),
            lambda c: c.mark_item("L1", "a/b", "complete"),
            lambda c: c.update_item_text("../x", "i1", "t"),
            lambda c: c.update_item_text("L1", "i1", "  "),
            lambda c: c.update_item_text("L1", "i?x", "t"),
            lambda c: c.update_item_text("L1", "i1", "Milk", header=None),
            lambda c: c.update_item_text("L1", "i1", "Milk", header="yes"),
        ],
    )
    async def test_rejects_before_io(self, client, mocked, call):
        with pytest.raises(CoziValidationError):
            await call(client)
        assert requests_made(mocked) == []

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.update_item_text("L1", "i1", "ghost"),
            lambda c: c.mark_item("L1", "i1", "complete"),
        ],
    )
    async def test_upsert_removes_the_phantom_item(self, client, mocked, call):
        mocked.put(api(I1), status=201, payload=item("ghost"))
        mocked.patch(api(L1), payload={**RAW, "items": []})
        with pytest.raises(ResourceNotFoundError, match="was removed") as e:
            await call(client)
        assert e.value.status_code == 201
        assert sent(mocked, "PATCH", L1)[0]["json"] == {"operations": [{"op": "remove", "path": "/items/i1"}]}

    async def test_upsert_cleanup_failure_still_raises_not_found(self, client, mocked):
        mocked.put(api(I1), status=201, payload=item("ghost"))
        mocked.patch(api(L1), payload={**RAW, "items": [item("ghost")]})  # survived
        with pytest.raises(ResourceNotFoundError, match="could NOT be removed"):
            await client.update_item_text("L1", "i1", "ghost")


@pytest.mark.parametrize(
    "call, method, path, response, body",
    [
        (
            lambda c: c.add_item("L1", "  milk\t"),
            "POST",
            ITEMS,
            item("milk"),
            {"text": "milk", "position": 0},
        ),
        (lambda c: c.add_item("L1", " MILK "), "POST", ITEMS, item("Milk"), {"text": "Milk", "position": 0}),
        (
            lambda c: c.add_item("L1", "\u00a0dairy ", header=True),
            "POST",
            ITEMS,
            item("DAIRY", itemType="header"),
            {"text": "DAIRY", "position": 0, "itemType": "header"},
        ),
        (
            lambda c: c.update_item_text("L1", "i1", " oat milk\n"),
            "PUT",
            I1,
            item("oat milk"),
            {"text": "oat milk"},
        ),
        (lambda c: c.rename_list("L1", " New "), "PUT", L1, {**RAW, "title": "New"}, {"title": "New"}),
        (lambda c: c.create_list(" New ", "todo"), "POST", LISTS, RAW, {"title": "New", "listType": "todo"}),
    ],
)
async def test_text_is_sent_stripped_as_cozi_stores_it(client, mocked, call, method, path, response, body):
    # Cozi strips the text it stores; comparing against the padded input would
    # fail a write that worked.
    mocked.add(api(path), method=method, status=201 if method == "POST" else 200, payload=response)
    await call(client)
    assert sent(mocked, method, path)[0]["json"] == body


class TestDeleteAndRemove:
    async def test_delete_item(self, client, mocked):
        mocked.delete(api(I1), status=204)
        assert await client.delete_item("L1", "i1") is True
        assert requests_made(mocked) == [("DELETE", "/2004/acct-123/list/L1/item/i1")]

    async def test_delete_item_404(self, client, mocked):
        mocked.delete(api(I1), status=404)
        with pytest.raises(ResourceNotFoundError):
            await client.delete_item("L1", "i1")

    async def test_remove_items(self, client, mocked):
        mocked.patch(api(L1), payload={**RAW, "items": [item("Keep", item_id="k")]})
        assert await client.remove_items("L1", ["a", "b"]) is True
        assert sent(mocked, "PATCH", L1)[0]["json"] == {
            "operations": [
                {"op": "remove", "path": "/items/a"},
                {"op": "remove", "path": "/items/b"},
            ]
        }

    @pytest.mark.parametrize("asked", ["b", "B"])
    async def test_survivor_raises(self, client, mocked, asked):
        # Cozi matches ids case-insensitively and answers in lower case.
        mocked.patch(api(L1), payload={**RAW, "items": [item(item_id="b")]})
        with pytest.raises(WriteVerificationError, match=f"1 of 2 item\\(s\\) from list L1: {asked}$"):
            await client.remove_items("L1", ["a", asked])

    async def test_non_list_body_is_a_parse_error(self, client, mocked):
        mocked.patch(api(L1), payload=["not", "a", "list object"])
        with pytest.raises(ResponseParseError):
            await client.remove_items("L1", ["a"])

    async def test_bad_request_surfaces(self, client, mocked):
        mocked.patch(api(L1), status=400, payload={"error": "bad", "detail": "pattern"})
        with pytest.raises(APIError) as e:
            await client.remove_items("L1", ["not-a-uuid"])
        assert e.value.status_code == 400

    async def test_empty_is_a_no_op(self, client, mocked):
        assert await client.remove_items("L1", []) is True
        assert requests_made(mocked) == []

    @pytest.mark.parametrize("ids", [{"a"}, ("a",), (i for i in ["a"])])
    async def test_any_iterable_of_ids_works(self, client, mocked, ids):
        mocked.patch(api(L1), payload={**RAW, "items": []})
        assert await client.remove_items("L1", ids) is True
        assert sent(mocked, "PATCH", L1)[0]["json"] == {"operations": [{"op": "remove", "path": "/items/a"}]}

    @pytest.mark.parametrize(
        "list_id, item_ids",
        [
            ("L1", "abc"),
            ("../x", []),
            ("L1", ["ok", "../x"]),
            ("L1", ["a~1b"]),
            ("L1", ["a/b"]),
            # Not iterable: must be a CoziValidationError, not a bare TypeError.
            ("L1", None),
            ("L1", 5),
        ],
    )
    async def test_rejects_before_io(self, client, mocked, list_id, item_ids):
        with pytest.raises(CoziValidationError):
            await client.remove_items(list_id, item_ids)
        assert requests_made(mocked) == []

    @pytest.mark.parametrize("bad", ["../x", "a?b", "a#b", "", None])
    async def test_delete_rejects_bad_ids(self, client, mocked, bad):
        for call in (client.delete_list(bad), client.delete_item("L1", bad), client.rename_list(bad, "t")):
            with pytest.raises(CoziValidationError):
                await call
        assert requests_made(mocked) == []
