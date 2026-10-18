#!/usr/bin/env python3
"""
A walk through the calendar API of py-cozi-client 3.0.

THIS DEMO WRITES TO YOUR COZI CALENDAR. In the month you pick (next month by
default) it creates a one-off appointment, an all-day appointment and a weekly
series, moves one occurrence of the series, shows the result, and then deletes
everything it created. It asks before writing, and leaves the rest of your
calendar alone.

Usage (from the repository root, after ``pip install -e .``)::

    COZI_USERNAME=you@example.com COZI_PASSWORD=... python examples/demo_calendar.py [YYYY-MM]

Without the environment variables it asks for the email and password; without a
month it asks for one.
"""

import asyncio
import getpass
import os
import sys
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from cozi_client import (
    WHOLE_SERIES,
    AuthenticationError,
    CoziAppointment,
    CoziClient,
    CoziException,
    Extent,
    Occurrence,
    repeat_rule,
)

PREFIX = "py-cozi-client demo"


def credentials():
    username = os.environ.get("COZI_USERNAME") or input("Cozi email: ").strip()
    password = os.environ.get("COZI_PASSWORD") or getpass.getpass("Cozi password: ")
    if not username or not password:
        sys.exit("An email and a password are needed.")
    return username, password


def pick_month() -> date:
    next_month = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
    if len(sys.argv) > 1:
        text = sys.argv[1]
    else:
        text = input(f"Month to write to, YYYY-MM [{next_month:%Y-%m}]: ").strip()
    if not text:
        return next_month
    try:
        return datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        sys.exit(f"Not a YYYY-MM month: {text!r}")


def describe(a: CoziAppointment) -> str:
    def hm(t):
        return t.strftime("%H:%M") if t else "--:--"

    when = "all day" if a.is_all_day else f"{hm(a.start_time)}-{hm(a.end_time)}"
    # occurrence_days: where the item falls on the page it was read from. A series
    # is one item however many times it repeats.
    days = ", ".join(f"{d:%b %d}" for d in a.occurrence_days or [a.start_day])
    repeat = f"  ({a.recurrence_text})" if a.recurrence_text else ""
    return f"  {a.subject}: {when} on {days}{repeat}"


async def show_month(cozi: CoziClient, month: date, only=None) -> None:
    items = await cozi.get_calendar(month.year, month.month)
    if only is not None:
        items = [a for a in items if a.id in only]
    for a in sorted(items, key=lambda a: (a.occurrence_days or [a.start_day])[0]):
        print(describe(a))
    if not items:
        print("  (nothing)")


async def clean_up(cozi: CoziClient, month: date, made: list) -> None:
    print("\nDeleting what the demo created.")
    # Newest first, so the moved occurrence goes before its series. Each item is
    # read again first: Cozi refuses a delete carrying a stale version, and the
    # occurrence edit changed the series.
    for item_id in reversed(made):
        try:
            page = {a.id: a for a in await cozi.get_calendar(month.year, month.month)}
            if item_id in page:
                appt = page[item_id]
                await cozi.delete_appointment(appt, WHOLE_SERIES if appt.is_recurring else None)
        except CoziException as e:
            print(f"  Could not delete one item: {e}")
    try:
        items = await cozi.get_calendar(month.year, month.month)
    except CoziException as e:
        print(f"  Could not check the result ({e}); look for {PREFIX!r} items in Cozi.")
        return
    left = [a for a in items if a.subject.startswith(PREFIX)]
    for a in left:
        print(f"  Still on your calendar, remove it by hand:{describe(a)}")
    if not left:
        print("  Your calendar is back as it was.")


async def demo(cozi: CoziClient, month: date) -> None:
    last_day = month.replace(day=monthrange(month.year, month.month)[1])
    print(f"\nYour calendar for {month:%B %Y}:")
    await show_month(cozi, month)

    print(
        f"\nNext the demo WRITES to your Cozi calendar in {month:%B %Y}: a few items whose "
        f"titles start with {PREFIX!r}. It deletes them again before it exits."
    )
    if input("Continue? [y/N] ").strip().lower() != "y":
        print("Nothing written.")
        return

    made = []  # ids of everything created, deleted in the finally
    try:
        one_off = await cozi.create_appointment(
            CoziAppointment(
                subject=f"{PREFIX}: dentist",
                start_day=month.replace(day=5),
                start_time=time(15),
                end_time=time(16),
            )
        )
        made.append(one_off.id)

        # No times makes it an all-day item.
        all_day = await cozi.create_appointment(
            CoziAppointment(subject=f"{PREFIX}: school holiday", start_day=month.replace(day=12))
        )
        made.append(all_day.id)

        start = month.replace(day=2)
        series = await cozi.create_appointment(
            CoziAppointment(
                subject=f"{PREFIX}: piano lesson",
                start_day=start,
                start_time=time(17),
                end_time=time(18),
                recurrence=repeat_rule("weekly", start_day=start, until=last_day),
            )
        )
        made.append(series.id)

        print("\nCreated:")
        for a in (one_off, all_day, series):
            print(describe(a))

        # Move one lesson an hour later. start_day is where that occurrence now
        # starts; Cozi stores the changed copy as a new item and drops the day from
        # the series.
        day = series.occurrence_days[1]
        series.start_day = day
        series.start_time, series.end_time = time(18), time(19)
        moved = await cozi.update_appointment(series, Occurrence(day, Extent.DAY))
        made.append(moved.id)

        print(f"\nMoved the {day:%b %d} lesson to 18:00. The demo's items now:")
        await show_month(cozi, month, only=set(made))
    finally:
        await clean_up(cozi, month, made)


async def main() -> None:
    print("This demo writes to your Cozi calendar, then deletes what it wrote.")
    username, password = credentials()
    month = pick_month()
    async with CoziClient(username, password) as cozi:
        try:
            await cozi.authenticate()
        except AuthenticationError as e:
            sys.exit(f"Login failed: {e}")
        print("Connected to Cozi.")
        try:
            await demo(cozi, month)
        except CoziException as e:
            sys.exit(f"Cozi error: {e}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
