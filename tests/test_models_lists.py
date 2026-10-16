"""List models and pure helpers (no HTTP mocking, so this also runs on aiohttp>=3.14)."""

import pytest

from cozi_client.exceptions import CoziValidationError
from cozi_client.lists import CoziItem, CoziList, ItemStatus, ListType, _item_text

# The shape GET /list/ returns (live 2026-10-16), plus an unknown key on each level.
RAW = {
    "listId": "L1",
    "title": "Groceries",
    "listType": "shopping",
    "notes": None,
    "owner": None,
    "version": 41,
    "future": {"x": 1},
    "items": [
        {
            "itemId": "h1",
            "text": "PRODUCE",
            "itemType": "header",
            "notes": None,
            "owner": None,
            "status": "incomplete",
            "version": 40,
        },
        {
            "itemId": "i1",
            "text": "Milk",
            "itemType": None,
            "notes": None,
            "owner": None,
            "status": "complete",
            "version": 41,
            "foo": 1,
        },
    ],
}


def dump(model):
    return model.model_dump(by_alias=True, mode="json", exclude_unset=True)


def test_enums_are_str_and_compare_both_ways():
    lst = CoziList.model_validate(RAW)
    assert lst.list_type is ListType.SHOPPING
    assert lst.list_type == "shopping"
    assert lst.items[1].status is ItemStatus.COMPLETE
    assert CoziList().list_type is ListType.TODO
    assert CoziItem().status is ItemStatus.INCOMPLETE


def test_unknown_enum_values_are_kept_not_fatal():
    lst = CoziList.model_validate({"listType": "wishlist", "items": [{"status": "archived"}]})
    assert (lst.list_type, lst.items[0].status) == ("wishlist", "archived")
    assert type(lst.list_type) is str


def test_is_header():
    lst = CoziList.model_validate(RAW)
    assert [i.is_header for i in lst.items] == [True, False]
    assert CoziItem(text="x").is_header is False


def test_round_trip_is_exact():
    assert dump(CoziList.model_validate(RAW)) == RAW


def test_reordered_items_keep_every_key():
    lst = CoziList.model_validate(RAW)
    lst.items = list(reversed(lst.items))
    assert dump(lst) == {**RAW, "items": [RAW["items"][1], RAW["items"][0]]}


def test_hand_built_item_dumps_only_what_was_set():
    item = CoziItem(text="Eggs")
    assert dump(item) == {"text": "Eggs"}
    item.status = ItemStatus.COMPLETE
    assert dump(item) == {"text": "Eggs", "status": "complete"}
    assert dump(CoziItem(id="i9", text="x")) == {"itemId": "i9", "text": "x"}


def test_unused_2x_fields_are_gone():
    assert set(CoziItem.model_fields) == {"id", "text", "status", "item_type", "version"}
    assert set(CoziList.model_fields) == {"id", "title", "list_type", "items", "version"}


@pytest.mark.parametrize(
    "text, header, expected",
    [
        ("produce", True, "PRODUCE"),
        ("Été", True, "ÉTÉ"),
        ("MILK", False, "Milk"),
        ("TV 2", False, "Tv 2"),
        ("Milk", False, "Milk"),
        ("iPhone", False, "iPhone"),
        ("2024", False, "2024"),
    ],
)
def test_item_text_matches_how_cozi_reads_case(text, header, expected):
    assert _item_text(text, header) == expected


@pytest.mark.parametrize("text, header", [("2024", True), ("A", False), ("X-1", False)])
def test_item_text_refuses_what_cozi_would_store_as_the_other_kind(text, header):
    with pytest.raises(CoziValidationError):
        _item_text(text, header)
