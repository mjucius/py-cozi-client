"""
Live: lists and items against the real Cozi API.

Only throwaway lists named subject("lists") are written. Every check reads the raw
GET /list/ back. Other domains create LIVE_TEST lists in the same account at the
same time, so order comparisons leave out every LIVE_TEST list except this test's own.
"""

import contextlib
import uuid

import pytest

from cozi_client.exceptions import APIError, ConflictError, ResourceNotFoundError
from cozi_client.lists import CoziItem, CoziList, ItemStatus, ListType
from tests.live.conftest import PREFIX, domain_sweeper, subject

_sweep = domain_sweeper("lists")

LISTS = "/2004/{acct}/list/"


async def raw_lists(cozi):
    return await cozi._request("GET", LISTS)


async def raw_list(cozi, list_id):
    return next((lst for lst in await raw_lists(cozi) if lst["listId"] == list_id), None)


async def raw_items(cozi, list_id):
    return [
        (i["itemId"], i["text"], i["status"], i["itemType"]) for i in (await raw_list(cozi, list_id))["items"]
    ]


def ids(lists, keep=(), list_type=None):
    """Ids in order, leaving out LIVE_TEST lists not in ``keep`` (other domains' fixtures)."""
    out = []
    for lst in lists:
        lid, title, ltype = (
            (lst["listId"], lst["title"], lst["listType"])
            if isinstance(lst, dict)
            else (lst.id, lst.title, lst.list_type)
        )
        if (PREFIX in title and lid not in keep) or (list_type and ltype != list_type):
            continue
        out.append(lid)
    return out


@contextlib.asynccontextmanager
async def throwaway(cozi, list_type=ListType.TODO):
    lst = await cozi.create_list(subject("lists"), list_type)
    try:
        yield lst
    finally:
        with contextlib.suppress(ResourceNotFoundError):
            await cozi._request("DELETE", LISTS + lst.id)


async def test_create_rename_delete(live_client):
    cozi = live_client
    title = subject("lists")
    created = await cozi.create_list(title, ListType.TODO)
    try:
        assert (created.title, created.list_type, created.items) == (title, ListType.TODO, [])
        await cozi.add_item(created.id, "kept one")
        await cozi.add_item(created.id, "kept two")
        before = await raw_list(cozi, created.id)

        new_title = subject("lists")
        renamed = await cozi.rename_list(created.id, f"  {new_title}\t")  # Cozi strips the title
        after = await raw_list(cozi, created.id)
        assert renamed.title == after["title"] == new_title
        assert after["listType"] == "todo"
        assert after["items"] == before["items"]
    finally:
        assert await cozi.delete_list(created.id) is True
    assert await raw_list(cozi, created.id) is None

    # Observed 2026-10-16: an unknown uuid is a 400 ("List type None is not valid"),
    # a non-uuid id a 404.
    with pytest.raises(APIError) as e:
        await cozi.rename_list(str(uuid.uuid4()), subject("lists"))
    assert e.value.status_code == 400
    with pytest.raises(ResourceNotFoundError):
        await cozi.delete_list("no-such-list-id")


async def test_items_with_a_header_and_full_put_reorder(live_client):
    cozi = live_client
    async with throwaway(cozi, ListType.SHOPPING) as lst:
        a = await cozi.add_item(lst.id, "alpha")
        h = await cozi.add_item(lst.id, "produce", position=0, header=True)
        b = await cozi.add_item(lst.id, "beta", position=2)
        assert (h.text, h.is_header, a.is_header) == ("PRODUCE", True, False)
        assert await raw_items(cozi, lst.id) == [
            (h.id, "PRODUCE", "incomplete", "header"),
            (a.id, "alpha", "incomplete", None),
            (b.id, "beta", "incomplete", None),
        ]

        current = next(x for x in await cozi.get_lists(ListType.SHOPPING) if x.id == lst.id)
        current.items = list(reversed(current.items))
        stored = await cozi.update_list(current)

        expected = [
            (b.id, "beta", "incomplete", None),
            (a.id, "alpha", "incomplete", None),
            (h.id, "PRODUCE", "incomplete", "header"),
        ]
        assert await raw_items(cozi, lst.id) == expected
        assert [(i.id, i.text, i.status, i.item_type) for i in stored.items] == expected


async def test_update_list_replaces_the_items_and_checks_the_version(live_client):
    cozi = live_client
    async with throwaway(cozi) as lst:
        x = await cozi.add_item(lst.id, "x")
        await cozi.add_item(lst.id, "y", position=1)
        stale = next(v for v in await cozi.get_lists() if v.id == lst.id)

        # An item left out of the PUT is deleted.
        current = stale.model_copy(deep=True)
        current.items = current.items[:1]
        await cozi.update_list(current)
        assert await raw_items(cozi, lst.id) == [(x.id, "x", "incomplete", None)]

        # An item with no id is created.
        current = next(v for v in await cozi.get_lists() if v.id == lst.id)
        current.items.append(CoziItem(text="made by put"))
        await cozi.update_list(current)
        items = await raw_items(cozi, lst.id)
        assert [i[1:] for i in items] == [("x", "incomplete", None), ("made by put", "incomplete", None)]
        assert items[1][0]

        # The snapshot from before both writes carries an old version.
        with pytest.raises(ConflictError) as e:
            await cozi.update_list(stale)
        assert e.value.status_code == 409
        assert await raw_items(cozi, lst.id) == items


async def test_list_type_filter_is_done_by_cozi(live_client):
    cozi = live_client
    async with throwaway(cozi, ListType.SHOPPING) as s, throwaway(cozi, ListType.TODO) as t:
        mine = {s.id, t.id}
        for list_type in ListType:
            server = await cozi.get_lists(list_type)
            everything = await cozi.get_lists()
            assert {x.list_type for x in server} == {list_type}
            assert ids(server, mine) == ids(everything, mine, list_type)
            assert len(mine & set(ids(server, mine))) == 1


async def test_mark_update_text_and_how_case_decides_the_kind(live_client):
    cozi = live_client
    async with throwaway(cozi) as lst:
        a = await cozi.add_item(lst.id, "alpha")
        assert (await cozi.mark_item(lst.id, a.id, ItemStatus.COMPLETE)).status is ItemStatus.COMPLETE
        assert await raw_items(cozi, lst.id) == [(a.id, "alpha", "complete", None)]
        assert (await cozi.mark_item(lst.id, a.id, "incomplete")).status is ItemStatus.INCOMPLETE
        assert await raw_items(cozi, lst.id) == [(a.id, "alpha", "incomplete", None)]

        await cozi.update_item_text(lst.id, a.id, "alpha renamed")
        assert await raw_items(cozi, lst.id) == [(a.id, "alpha renamed", "incomplete", None)]
        await cozi.update_item_text(lst.id, a.id, "TV")
        assert await raw_items(cozi, lst.id) == [(a.id, "Tv", "incomplete", None)]
        await cozi.update_item_text(lst.id, a.id, "dairy", header=True)
        assert await raw_items(cozi, lst.id) == [(a.id, "DAIRY", "incomplete", "header")]

        # Cozi strips the text (tab, NBSP and em space included), so padded input
        # must not fail verification of a write that worked.
        await cozi.update_item_text(lst.id, a.id, " \u2003dairy again\t")
        assert await raw_items(cozi, lst.id) == [(a.id, "dairy again", "incomplete", None)]
        p = await cozi.add_item(lst.id, "\u00a0 MILK \n", position=1)
        assert await raw_items(cozi, lst.id) == [
            (a.id, "dairy again", "incomplete", None),
            (p.id, "Milk", "incomplete", None),
        ]

        # The wire rule itself (bundle-only until now): all caps is a header, whatever
        # itemType says.
        path = f"{LISTS}{lst.id}/item/"
        caps = await cozi._request("POST", path, json={"text": "LIVE CAPS", "position": 0})
        lower = await cozi._request(
            "POST", path, json={"text": "lower header", "position": 0, "itemType": "header"}
        )
        assert (caps["itemType"], lower["itemType"]) == ("header", None)


async def test_delete_item_and_unknown_ids(live_client):
    cozi = live_client
    async with throwaway(cozi) as lst:
        a = await cozi.add_item(lst.id, "a")
        b = await cozi.add_item(lst.id, "b", position=1)
        assert await cozi.delete_item(lst.id, a.id) is True
        assert await raw_items(cozi, lst.id) == [(b.id, "b", "incomplete", None)]

        # Observed 2026-10-16: an unknown uuid answers 204, a non-uuid 404.
        assert await cozi.delete_item(lst.id, str(uuid.uuid4())) is True
        with pytest.raises(ResourceNotFoundError) as e:
            await cozi.delete_item(lst.id, "no-such-item-id")
        assert e.value.status_code == 404
        assert await raw_items(cozi, lst.id) == [(b.id, "b", "incomplete", None)]


async def test_remove_items(live_client):
    cozi = live_client
    async with throwaway(cozi) as lst:
        a = await cozi.add_item(lst.id, "a")
        b = await cozi.add_item(lst.id, "b", position=1)
        c = await cozi.add_item(lst.id, "c", position=2)
        assert await cozi.remove_items(lst.id, [a.id, c.id, str(uuid.uuid4())]) is True
        assert await raw_items(cozi, lst.id) == [(b.id, "b", "incomplete", None)]

        # Ids match case-insensitively; the survivor check must agree.
        assert await cozi.remove_items(lst.id, [b.id.upper()]) is True
        assert await raw_items(cozi, lst.id) == []

        # Cozi validates the pointer: only /items/<uuid> is accepted.
        with pytest.raises(APIError) as e:
            await cozi.remove_items(lst.id, ["not-a-uuid"])
        assert e.value.status_code == 400


async def test_put_to_an_unknown_item_creates_nothing(live_client):
    cozi = live_client
    async with throwaway(cozi) as lst:
        k = await cozi.add_item(lst.id, "keep")
        kept = [(k.id, "keep", "incomplete", None)]
        for call in (
            lambda ghost: cozi.update_item_text(lst.id, ghost, "ghost"),
            lambda ghost: cozi.mark_item(lst.id, ghost, ItemStatus.COMPLETE),
        ):
            with pytest.raises(ResourceNotFoundError, match="was removed") as e:
                await call(str(uuid.uuid4()))
            assert e.value.status_code == 201
            assert await raw_items(cozi, lst.id) == kept

        # A non-uuid id is a plain 404.
        with pytest.raises(ResourceNotFoundError) as e:
            await cozi.update_item_text(lst.id, "no-such-item-id", "ghost")
        assert e.value.status_code == 404
        assert await raw_items(cozi, lst.id) == kept


async def test_update_list_on_an_unknown_id_creates_nothing(live_client):
    cozi = live_client
    ghost = str(uuid.uuid4())
    phantom = CoziList(id=ghost, title=subject("lists"), list_type=ListType.TODO, items=[])
    with pytest.raises(ResourceNotFoundError, match="was removed") as e:
        await cozi.update_list(phantom)
    assert e.value.status_code == 201
    assert await raw_list(cozi, ghost) is None


async def test_reorder_list_move_and_restore(live_client):
    """Runs last: it moves two throwaway lists and checks the real lists never move."""
    cozi = live_client
    real_before = ids(await raw_lists(cozi))
    try:
        async with throwaway(cozi) as t1, throwaway(cozi) as t2:
            mine = (t1.id, t2.id)
            todo = ids(await raw_lists(cozi), mine, "todo")
            assert todo[-2:] == [t1.id, t2.id]  # new lists go last in their type

            # Move t2 to t1's index within the to-do lists. Under a global index it
            # would land among the shopping lists instead.
            k = [x["listId"] for x in await raw_lists(cozi) if x["listType"] == "todo"].index(t1.id)
            moved = await cozi.reorder_list(t2.id, k)
            moved_todo = [x.id for x in moved if x.list_type == ListType.TODO]
            assert moved_todo[k : k + 2] == [t2.id, t1.id]
            assert ids(moved, mine) == ids(await raw_lists(cozi), mine)

            back = await cozi.reorder_list(t2.id, k + 1)
            back_todo = [x.id for x in back if x.list_type == ListType.TODO]
            assert back_todo[k : k + 2] == [t1.id, t2.id]

            last = await cozi.reorder_list(t1.id, 1000)  # past the end: moved last
            assert [x.id for x in last if x.list_type == ListType.TODO][-1] == t1.id
            assert ids(last) == real_before
    finally:
        real_after = ids(await raw_lists(cozi))
        assert real_after == real_before, (
            "The real lists changed order during the reorder test. "
            f"Before: {real_before} After: {real_after}"
        )
