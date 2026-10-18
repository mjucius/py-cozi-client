#!/usr/bin/env python3
"""
A walk through the lists API of py-cozi-client 3.0.

It prints your lists, then works on a list of its own that it creates for the demo:
adds a section header and items, checks one off, renames the list, reorders the
items, removes the checked ones, and finally deletes the demo list. Your other
lists are only read.

Usage (from the repository root, after ``pip install -e .``)::

    COZI_USERNAME=you@example.com COZI_PASSWORD=... python examples/demo_lists.py

Without the environment variables it asks for the email and password.
"""

import asyncio
import getpass
import os
import sys

from cozi_client import AuthenticationError, CoziClient, CoziException, CoziList, ItemStatus, ListType

DEMO_TITLE = "py-cozi-client demo list"


def credentials():
    username = os.environ.get("COZI_USERNAME") or input("Cozi email: ").strip()
    password = os.environ.get("COZI_PASSWORD") or getpass.getpass("Cozi password: ")
    if not username or not password:
        sys.exit("An email and a password are needed.")
    return username, password


def show(lst: CoziList) -> None:
    print(f"  {lst.title}")
    for item in lst.items:
        if item.is_header:
            print(f"    {item.text}")
        else:
            box = "x" if item.status == ItemStatus.COMPLETE else " "
            print(f"      [{box}] {item.text}")


async def read_list(cozi: CoziClient, list_id: str) -> CoziList:
    # There is no single-list read; this also picks up the list's current version,
    # which update_list needs (Cozi refuses a stale one with ConflictError).
    return next(lst for lst in await cozi.get_lists(ListType.SHOPPING) if lst.id == list_id)


async def demo(cozi: CoziClient) -> None:
    print("\nYour lists:")
    for list_type in ListType:
        lists = await cozi.get_lists(list_type)
        print(f"  {list_type.value}: {len(lists)} list(s)")
        for lst in lists:
            done = sum(1 for i in lst.items if i.status == ItemStatus.COMPLETE and not i.is_header)
            total = sum(1 for i in lst.items if not i.is_header)
            print(f"    {lst.title} ({done}/{total} done)")

    print(f"\nCreating the shopping list {DEMO_TITLE!r}.")
    created = await cozi.create_list(DEMO_TITLE, ListType.SHOPPING)
    list_id = created.id
    try:
        # A header is stored in capitals ("PRODUCE"); Cozi decides that from the text.
        await cozi.add_item(list_id, "Produce", position=0, header=True)
        # position counts from the top, so give each item its own slot below the header.
        items = {}
        for position, text in enumerate(["Apples", "Bananas", "Carrots"], start=1):
            items[text] = await cozi.add_item(list_id, text, position=position)
        show(await read_list(cozi, list_id))

        print("\nChecking off Bananas.")
        await cozi.mark_item(list_id, items["Bananas"].id, ItemStatus.COMPLETE)

        print("Renaming the list.")
        await cozi.rename_list(list_id, DEMO_TITLE + " (renamed)")

        print("Reversing the items under the header.")
        lst = await read_list(cozi, list_id)
        headers = [i for i in lst.items if i.is_header]
        others = [i for i in lst.items if not i.is_header]
        lst.items = headers + others[::-1]  # update_list writes the whole item array back
        lst = await cozi.update_list(lst)
        show(lst)

        print("\nRemoving the checked items.")
        checked = [i.id for i in lst.items if i.status == ItemStatus.COMPLETE]
        await cozi.remove_items(list_id, checked)
        show(await read_list(cozi, list_id))
    finally:
        print("\nDeleting the demo list.")
        try:
            await cozi.delete_list(list_id)
        except CoziException as e:
            print(f"Could not delete it ({e}); remove the demo list in Cozi by hand.")


async def main() -> None:
    username, password = credentials()
    async with CoziClient(username, password) as cozi:
        try:
            await cozi.authenticate()
        except AuthenticationError as e:
            sys.exit(f"Login failed: {e}")
        print("Connected to Cozi.")
        try:
            await demo(cozi)
        except CoziException as e:
            sys.exit(f"Cozi error: {e}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
