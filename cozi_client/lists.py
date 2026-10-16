"""
Shopping and to-do lists, read and written the way my.cozi.com does it.

Things Cozi does that the endpoints don't make obvious (all seen live on 2026-10-16):

- Cozi decides from the text alone whether an item is a section header. An item is a
  header (itemType "header") exactly when it has a letter and all its letters are
  upper case. Cozi ignores any itemType sent with the item.
- Cozi strips leading and trailing whitespace from item text and list titles.
- ``PUT /list/{id}`` replaces the whole item array, and answers 409 to a stale
  ``version``.
- A PUT to an item, or to a list with a listType, whose uuid Cozi does not know
  creates it (status 201).
"""

from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type, Union

from pydantic import BaseModel, ConfigDict, Field

from ._transport import Transport, logger, parse_model, parse_models, validate_id
from .exceptions import (
    CoziException,
    CoziValidationError,
    ResourceNotFoundError,
    WriteVerificationError,
)

LISTS = "/2004/{acct}/list/"


class ListType(str, Enum):
    SHOPPING = "shopping"
    TODO = "todo"


class ItemStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class CoziItem(BaseModel):
    """
    One item on a list.

    Cozi also sends keys that have no field here (notes, owner). They are kept as
    extras, and update_list writes them back unchanged.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: Optional[str] = Field(None, alias="itemId")
    text: str = ""
    # left_to_right: a known value becomes the enum. An unknown value stays a str,
    # so one odd item can't make the whole get_lists() call fail.
    status: Union[ItemStatus, str] = Field(ItemStatus.INCOMPLETE, union_mode="left_to_right")
    item_type: Optional[str] = Field(None, alias="itemType")
    version: Optional[int] = None

    @property
    def is_header(self) -> bool:
        """A section heading. It has no checkbox."""
        return self.item_type == "header"


class CoziList(BaseModel):
    """A shopping or to-do list. Unknown keys are kept as extras, as on CoziItem."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: Optional[str] = Field(None, alias="listId")
    title: str = ""
    list_type: Union[ListType, str] = Field(ListType.TODO, alias="listType", union_mode="left_to_right")
    items: List[CoziItem] = Field(default_factory=list)
    version: Optional[int] = None


def _value(enum: Type[Enum], value: Any, name: str) -> str:
    # The plain wire string. On 3.11+ a (str, Enum) member formats as "ListType.TODO"
    # in a URL query or an f-string.
    try:
        return enum(value).value
    except (ValueError, TypeError):
        raise CoziValidationError(f"Invalid {name}: {value!r}") from None


def _text(value: Any, name: str) -> str:
    # Cozi strips item text and list titles (str.strip() semantics, seen live), so
    # the stripped text is what gets sent, cased and verified.
    if not isinstance(value, str) or not value.strip():
        raise CoziValidationError(f"{name} cannot be empty")
    return value.strip()


def _index(value: Any, name: str) -> int:
    # bool is an int subclass; True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoziValidationError(f"Invalid {name}: expected an integer >= 0, got {value!r}")
    return value


def _item_text(text: str, header: bool) -> str:
    """
    The text that gives the item the kind the caller asked for.

    Cozi makes all-caps text a header, so we do what the web client's je() does: a
    header is sent in upper case, and an all-caps plain item in sentence case
    ("MILK" -> "Milk").
    """
    if header:
        text = text.upper()
        if not text.isupper():
            raise CoziValidationError(f"A section header needs at least one letter: {text!r}")
        return text
    if text.isupper():
        text = text[0].upper() + text[1:].lower()
    if text.isupper():  # one cased letter ("A", "X-1"): sentence case is still all caps
        raise CoziValidationError(
            f"Cozi would store {text!r} as a section header; pass header=True or add a lower-case letter"
        )
    return text


def _verify_item(item: CoziItem, text: str, header: bool, what: str) -> None:
    problems = []
    if item.text != text:
        problems.append(f"text {item.text!r} instead of {text!r}")
    if item.is_header != header:
        problems.append("a header" if item.is_header else "not a header")
    if not item.id:
        problems.append("no id")
    if problems:
        raise WriteVerificationError(
            f"Cozi did not apply the {what}: it returned {', '.join(problems)}",
            response_data=item.model_dump(by_alias=True, mode="json"),
        )


class ListsMixin(Transport):
    """Lists and list items. Mixed into CoziClient."""

    async def get_lists(self, list_type: Union[ListType, str, None] = None) -> List[CoziList]:
        """
        Every list, in the user's order. With ``list_type``, only that type's lists.

        Cozi does the filtering: ``?list_type=`` returns the same lists, in the same
        order, as filtering everything on the client (checked live).
        """
        params = None
        if list_type is not None:
            params = {"list_type": _value(ListType, list_type, "list_type")}
        return parse_models(CoziList, await self._request("GET", LISTS, params=params))

    async def create_list(self, title: str, list_type: Union[ListType, str]) -> CoziList:
        """Create an empty list. Cozi puts it after the other lists of its type."""
        body = {
            "title": _text(title, "List title"),
            "listType": _value(ListType, list_type, "list_type"),
        }
        return parse_model(CoziList, await self._request("POST", LISTS, json=body))

    async def rename_list(self, list_id: str, title: str) -> CoziList:
        """Change only the title. A title-only PUT leaves the items and the type alone."""
        list_id = validate_id(list_id, "list_id")
        title = _text(title, "List title")
        lst = await self._put_list(list_id, {"title": title})
        if lst.title != title:
            raise WriteVerificationError(
                f"Cozi did not apply the rename: asked for {title!r}, server returned {lst.title!r}"
            )
        return lst

    async def update_list(self, lst: CoziList) -> CoziList:
        """
        Write back a whole list. The web client does this to reorder items and to
        check or uncheck all of them.

        Cozi replaces the list's items with ``lst.items``. An item left out is
        deleted, and an item with no id is created. If ``version`` is older than
        Cozi's, the write is refused with ConflictError (409), so read the list
        again just before changing it. Only the keys Cozi sent, or that you set,
        are written back.
        """
        if not isinstance(lst, CoziList):
            raise CoziValidationError(f"Expected a CoziList, got {type(lst).__name__}")
        list_id = validate_id(lst.id, "list_id")
        return await self._put_list(list_id, lst.model_dump(by_alias=True, mode="json", exclude_unset=True))

    async def delete_list(self, list_id: str) -> bool:
        """Delete a list and its items."""
        await self._request("DELETE", LISTS + validate_id(list_id, "list_id"))
        return True

    async def reorder_list(self, list_id: str, new_index: int) -> List[CoziList]:
        """
        Move a list to position ``new_index`` among the lists of its own type
        (0 = first). Returns every list, in the new order.

        This is the web client's drag and drop: one move operation whose ``from`` is
        the list id. Checked live: the index counts within the type, and an index
        past the end moves the list last. An unknown list id gets a 500.
        """
        list_id = validate_id(list_id, "list_id")
        new_index = _index(new_index, "new_index")
        body = {"operations": [{"op": "move", "from": list_id, "path": str(new_index)}]}
        return parse_models(CoziList, await self._request("PATCH", LISTS, json=body))

    async def add_item(self, list_id: str, text: str, position: int = 0, header: bool = False) -> CoziItem:
        """
        Add an item at ``position`` (0 = top), or a section header if ``header`` is set.

        The text sent depends on the kind (see _item_text): a header goes in upper
        case, and an all-caps plain item in sentence case.
        """
        list_id = validate_id(list_id, "list_id")
        text = _item_text(_text(text, "Item text"), header)
        body: Dict[str, Any] = {"text": text, "position": _index(position, "position")}
        if header:
            body["itemType"] = "header"  # ignored by Cozi; sent as the web client sends it
        item = parse_model(CoziItem, await self._request("POST", f"{LISTS}{list_id}/item/", json=body))
        _verify_item(item, text, header, "item creation")
        return item

    async def update_item_text(self, list_id: str, item_id: str, text: str, header: bool = False) -> CoziItem:
        """
        Change an item's text. Afterwards the item is a header if ``header`` is set,
        and a plain item otherwise, because Cozi decides the kind from the new text.
        """
        text = _item_text(_text(text, "Item text"), header)
        item = await self._put_item(list_id, item_id, {"text": text})
        _verify_item(item, text, header, "text update")
        return item

    async def mark_item(self, list_id: str, item_id: str, status: Union[ItemStatus, str]) -> CoziItem:
        """Check (``complete``) or uncheck (``incomplete``) an item."""
        status = _value(ItemStatus, status, "status")
        item = await self._put_item(list_id, item_id, {"status": status})
        if item.status != status:
            returned = getattr(item.status, "value", item.status)
            raise WriteVerificationError(
                f"Cozi did not apply the status update: asked for {status!r}, "
                f"server returned {returned!r}"
            )
        return item

    async def delete_item(self, list_id: str, item_id: str) -> bool:
        """
        Delete one item, as the web client does.

        Cozi answers 204 for any well-formed uuid, even one that is not on the list,
        so True does not prove the item existed. An id that is not a uuid gets 404
        (ResourceNotFoundError).
        """
        list_id = validate_id(list_id, "list_id")
        item_id = validate_id(item_id, "item_id")
        await self._request("DELETE", f"{LISTS}{list_id}/item/{item_id}")
        return True

    async def remove_items(self, list_id: str, item_ids: List[str]) -> bool:
        """
        Remove several items in one PATCH, as "delete checked items" does.

        Cozi ignores a uuid that is not on the list. Any id that survives, checked
        against the list Cozi returns, raises WriteVerificationError.
        """
        list_id = validate_id(list_id, "list_id")
        if isinstance(item_ids, str):
            raise CoziValidationError("item_ids must be a list of ids, not a single string")
        # Each id lands in a JSON-Pointer path. validate_id rules out "/" and "~",
        # which could otherwise point the remove at a different node.
        ids = [validate_id(i, "item_id") for i in item_ids]
        if not ids:
            return True
        operations = [{"op": "remove", "path": f"/items/{i}"} for i in ids]
        lst = parse_model(
            CoziList,
            await self._request("PATCH", LISTS + list_id, json={"operations": operations}),
        )
        # Cozi matches item ids case-insensitively and returns them in lower case.
        remaining = {(item.id or "").lower() for item in lst.items}
        survived = [i for i in ids if i.lower() in remaining]
        if survived:
            raise WriteVerificationError(
                f"Cozi did not remove {len(survived)} of {len(ids)} item(s) from list "
                f"{list_id}: {', '.join(survived)}",
                response_data=lst.model_dump(by_alias=True, mode="json"),
            )
        return True

    # ------------------------------------------------------------- upsert guard

    async def _put_list(self, list_id: str, body: Dict[str, Any]) -> CoziList:
        response = await self._put_existing(
            LISTS + list_id, body, f"List {list_id}", lambda: self.delete_list(list_id)
        )
        return parse_model(CoziList, response)

    async def _put_item(self, list_id: str, item_id: str, body: Dict[str, Any]) -> CoziItem:
        list_id = validate_id(list_id, "list_id")
        item_id = validate_id(item_id, "item_id")
        response = await self._put_existing(
            f"{LISTS}{list_id}/item/{item_id}",
            body,
            f"Item {item_id} in list {list_id}",
            lambda: self.remove_items(list_id, [item_id]),
        )
        return parse_model(CoziItem, response)

    async def _put_existing(
        self, path: str, body: Dict[str, Any], what: str, cleanup: Callable[[], Awaitable[Any]]
    ) -> Any:
        """
        PUT an update, and refuse it if Cozi created the target instead.

        A PUT to an unknown uuid is an upsert. Cozi answers 201, and the body looks
        just like an update's. The phantom is deleted before ResourceNotFoundError
        is raised. If the cleanup fails, the original error is still raised, and its
        message says the phantom may remain.
        """
        status, response = await self._request_with_status("PUT", path, json=body)
        if status != 201:
            return response
        try:
            await cleanup()
            note = " What it created was removed."
        except CoziException as e:
            logger.warning("Could not remove the phantom created at %s: %s", path, type(e).__name__)
            note = " What it created could NOT be removed and may still be there."
        raise ResourceNotFoundError(
            f"{what} does not exist; Cozi created it instead of updating it.{note}",
            status_code=status,
        )
