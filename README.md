# py-cozi-client

An unofficial async Python client for the [Cozi Family Organizer](https://www.cozi.com/) API.

Version 3 talks to `rest.cozi.com` the way the production web client at `my.cozi.com` does
(bundle `main.c43180f9`): the same endpoints, request bodies, headers and token handling.
Where the bundle left a server behaviour open, it was settled against a real account; the
notes below about what Cozi does come from those runs.

- Lists and items, the calendar (appointments, recurring series, birthdays, meals, search,
  smart add, print view), family members, the household, subscribed calendars, iCal feeds and
  the recipe box.
- Pydantic 2 models, full type hints (`py.typed`).
- Every write returns what Cozi stored, and writes Cozi silently discards raise instead of
  passing as success.
- Every error raised is a `CoziException`.

## Installation

```bash
pip install py-cozi-client
```

## Quick Start

```python
import asyncio
from datetime import date, time

from cozi_client import CoziClient, CoziAppointment, ItemStatus, ListType


async def main():
    async with CoziClient("you@example.com", "your-password") as client:
        # No login call needed: the first request logs in.
        groceries = await client.create_list("Groceries", ListType.SHOPPING)
        milk = await client.add_item(groceries.id, "Milk")
        await client.add_item(groceries.id, "Dairy", header=True)  # stored as the header "DAIRY"
        await client.mark_item(groceries.id, milk.id, ItemStatus.COMPLETE)

        for lst in await client.get_lists(ListType.SHOPPING):
            print(lst.title, [item.text for item in lst.items])

        dentist = await client.create_appointment(
            CoziAppointment(
                subject="Dentist",
                start_day=date(2026, 11, 3),
                start_time=time(15, 0),
                end_time=time(16, 0),
            )
        )
        print(dentist.id, dentist.occurrence_days)


asyncio.run(main())
```

The installed version is `cozi_client.__version__`.

## API reference

Each `await client.…` entry is a coroutine method of `CoziClient`; the rest are plain
functions and constants importable from `cozi_client`. Ids are checked against `ID_PATTERN`
before any request is sent.

### Client and session

```python
CoziClient(
    username: str,
    password: str,
    session: aiohttp.ClientSession | None = None,
    retry_attempts: int = 3,
    request_timeout: float = 30,
)
```

Use it as `async with CoziClient(...) as client:`. A session the client created is closed on
exit; an injected `session` is left open. `request_timeout` is the total seconds per HTTP
attempt. `client.account_id` is the household id once logged in.

- `await client.authenticate()`: log in now. Optional, since every method logs in on first use.
- `await client.logout()`: end the session on the server (this revokes the refresh cookie),
  then forget the token and cookies. The next call logs in again with the stored password.
- `await client.change_password(new_password: str)`: change the password. The client adopts
  the token Cozi returns and uses the new password for later logins.
- `await client.request_password_reset(email: str)`: have Cozi email a reset link. No login needed.
- `await client.submit_password_reset(account_id: str, request_id: str, new_password: str)`:
  set a new password from the ids in a reset link. No login needed.
- `await client.close()`: close the session if the client created it. `async with` does this for you.

### Lists and items

- `await client.get_lists(list_type: ListType | str | None = None) -> list[CoziList]`: every
  list, in the user's order. With `list_type`, Cozi returns only lists of that type.
- `await client.create_list(title: str, list_type: ListType | str) -> CoziList`: an empty
  list, placed after the other lists of its type.
- `await client.rename_list(list_id: str, title: str) -> CoziList`: change only the title.
  Items and type are left alone.
- `await client.update_list(lst: CoziList) -> CoziList`: write back a whole list (reorder
  items, check or uncheck them all). Read [update_list replaces the whole list](#update_list-replaces-the-whole-list) first.
- `await client.delete_list(list_id: str) -> bool`: delete a list and its items.
- `await client.reorder_list(list_id: str, new_index: int) -> list[CoziList]`: move a list to
  `new_index` among the lists of its own type (0 is first; past the end moves it last).
  Returns every list in the new order.
- `await client.add_item(list_id: str, text: str, position: int = 0, header: bool = False) -> CoziItem`:
  add an item at `position` (0 is the top), or a section header when `header=True`.
- `await client.update_item_text(list_id: str, item_id: str, text: str, header: bool = False) -> CoziItem`:
  change an item's text. Afterwards it is a header only if `header=True`.
- `await client.mark_item(list_id: str, item_id: str, status: ItemStatus | str) -> CoziItem`:
  check (`ItemStatus.COMPLETE`) or uncheck (`ItemStatus.INCOMPLETE`) an item.
- `await client.delete_item(list_id: str, item_id: str) -> bool`: delete one item. Cozi answers
  success for any well-formed uuid, so `True` does not prove the item existed.
- `await client.remove_items(list_id: str, item_ids: list[str]) -> bool`: remove several items
  in one request, as "delete checked items" does. Raises `WriteVerificationError` if any id is
  still on the list afterwards.

`CoziList` has `id`, `title`, `list_type`, `items` and `version`. `CoziItem` has `id`, `text`,
`status`, `item_type`, `version` and `is_header`. Other keys Cozi sends (such as `notes` and
`owner`) are kept as pydantic extras and written back unchanged.

### Calendar

Reads:

- `await client.get_calendar(year: int, month: int) -> list[CoziAppointment]`: every item on a
  month page. A series or multi-day item appears once, and its `occurrence_days` lists the
  start day of each occurrence that month. To combine months, merge the pages by `id`.
- `await client.search_calendar(query: str) -> tuple[list[CoziAppointment], bool]`: Cozi's
  calendar search, returned as `(items, has_more)`. There is no way to fetch more pages. The
  index lags writes by a few seconds and reaches back only a few years. Paid plan.
- `await client.get_calendar_print(view: str, day: date) -> bytes`: the printable `"week"` or
  `"month"` containing `day`, as the PDF Cozi renders.
- `await client.parse_event(text: str, now: datetime | None = None) -> CoziAppointment`: smart
  add. Turns free text ("dentist tuesday 3pm") into an unsaved item, which you can pass to
  `create_appointment`. `now` anchors relative dates and defaults to local time. Paid plan.

Appointments:

- `await client.create_appointment(appt: CoziAppointment, notify: Sequence[str] | None = None) -> CoziAppointment`:
  create `appt` and return the item Cozi stored, with its new id. Set `recurrence` (see
  `repeat_rule`) for a series and `reminders` as minutes before. `notify` lists the person ids
  Cozi should tell about the change.
- `await client.update_appointment(appt: CoziAppointment, scope: Scope = None, notify: Sequence[str] | None = None) -> CoziAppointment`:
  replace an appointment or birthday with `appt` and return what Cozi stored. Start from an item
  read with `get_calendar`. Reminders are re-sent unless they are `None`; `[]` clears them.
- `await client.delete_appointment(appt: CoziAppointment, scope: Scope = None, notify: Sequence[str] | None = None) -> bool`:
  delete an appointment or birthday.

Recurring series. On a series, `update_appointment` and `delete_appointment` need a `scope`,
and raise `CoziValidationError` without one:

| `scope` | Touches |
|---|---|
| `Occurrence(day, Extent.DAY)` | that occurrence only; an edit returns the stand-in Cozi stores for it, which has a new id |
| `Occurrence(day, Extent.FORWARD)` | that occurrence and every later one; an edit returns the new series |
| `WHOLE_SERIES` | every occurrence, past ones included |
| `None` | a one-off item (or a birthday) |

`day` must be one of `appt.occurrence_days`, so read the item from the occurrence's own month.
For an occurrence edit, set `appt.start_day` to where that occurrence should start (normally `day`):

```python
from cozi_client import CoziAppointment, Extent, Occurrence, WHOLE_SERIES, repeat_rule

swim = await client.create_appointment(
    CoziAppointment(
        subject="Swim",
        start_day=date(2026, 11, 2),
        start_time=time(17, 0),
        end_time=time(18, 0),
        recurrence=repeat_rule("weekly", start_day=date(2026, 11, 2), weekdays=["MO", "WE"]),
    )
)
day = swim.occurrence_days[1]
swim.start_day, swim.start_time, swim.end_time = day, time(18, 0), time(19, 0)
standin = await client.update_appointment(swim, scope=Occurrence(day, Extent.DAY))

# The series changed, so read it again before the next write.
series = next(a for a in await client.get_calendar(2026, 11) if a.id == swim.id)
await client.delete_appointment(series, scope=WHOLE_SERIES)
```

- `repeat_rule(frequency, *, start_day, interval=1, weekdays=None, monthly_by="date", until=None) -> dict`
  builds a `recurrence` in the web client's shape. `frequency` is `"daily"`, `"weekly"`,
  `"monthly"` or `"yearly"`. `weekdays` (weekly only) are two-letter days and must include the
  start's weekday. `monthly_by="weekday"` repeats on the nth weekday of the month. `until` is
  the last day of the series.
- `last_day_of(start, date_span, all_day) -> date` and
  `date_span_for_last_day(start, last_day, all_day) -> int` convert between `date_span` and the
  last day an item covers (see [date_span is a day offset](#date_span-is-a-day-offset)).

Birthdays and meals:

- `await client.create_birthday(name: str, day: date, birth_year: int | None = None, reminders: Sequence[int] = (10080, 1440)) -> CoziAppointment`:
  a yearly birthday on `day`'s month and day. The default reminders are a week and a day
  before. Cozi starts the series in `birth_year`, or in 1904 when there is none, and
  `start_day` reads back with that year. To edit or delete a birthday, use
  `update_appointment` or `delete_appointment` with no scope.
- `await client.create_meal(day: date, slot: str, items: Sequence[str | tuple[str, str | None]], notes: str = "") -> CoziAppointment`:
  plan a meal. `slot` is `breakfast`, `lunch`, `dinner` or `snack` (times in `MEAL_SLOTS`).
  `items` are dish texts, or `(text, recipe_box_id)` for a dish from the recipe box.
- `await client.update_meal(meal: CoziAppointment) -> CoziAppointment`: replace a meal read
  from `get_calendar`.
- `await client.delete_meal(meal: CoziAppointment) -> bool`: delete a meal read from `get_calendar`.

`CoziAppointment` fields: `id`, `subject`, `start_day`, `start_time` and `end_time` (both
`None` for an all-day item), `date_span`, `attendees` (person ids), `location`, `notes`,
`item_type` (`"appointment"`, `"birthday"` or `"meal"`), `item_source` (set on
subscribed-calendar items), `item_version`, `reminders` (minutes before), `recurrence`,
`recurrence_start_day`, `read_only`, `name` and `birth_year` (birthdays), `meal_slot` and
`meal_items` (meals), and `occurrence_days`. Properties: `is_all_day`, `is_recurring`,
`skipped_days` (deleted occurrences), `recurrence_text` (Cozi's wording of the rule), `end_day`
(last day of the series), `last_day`, and `is_editable` (false for subscribed, read-only and
meal items).

### Account, household and calendars

Family members:

- `await client.get_family_members() -> list[CoziPerson]`: every family member.
- `await client.create_person(name: str, email: str = "", color_index: int | None = None, settings: PersonSettings | None = None) -> CoziPerson`:
  add a member and return the stored record. Defaults match the web client: the first palette
  colour no other member uses, and `default_settings(email)`. A non-empty `email` gives that
  address a login to the household, and Cozi may send it a verification email.
- `await client.update_person(person: CoziPerson) -> CoziPerson`: save a member's name, email,
  colour and settings. Clearing an email a member already has is refused, because it removes
  their login.
- `await client.delete_person(person_id: str) -> None`: remove a member. Refused for the last
  member, and for the only member with an email (the household's last login).
- `await client.send_verification_email(person_id: str) -> None`: have Cozi email the member a
  verification link. This sends real mail.

Household and account:

- `await client.get_household() -> CoziHousehold`: name, colour, timezone, cobrand and photo.
- `await client.update_household(*, name: str | None = None, color_index: int | None = None, timezone: str | None = None) -> CoziHousehold`:
  change any of these. `timezone` is an IANA name, and changing it moves when reminders fire.
- `await client.upload_household_photo(data: bytes, filename: str) -> CoziHousehold`: a
  `.jpg`, `.jpeg` or `.png` of at most 20 MB (`MAX_PHOTO_BYTES`) becomes the family photo. The
  previous photo cannot be put back.
- `await client.get_config() -> dict`: the account config (feature keys, the attendee colour palette and so on).
- `await client.get_personal_info() -> dict`: family name, names, external ids and devices. This is PII, so do not log it.
- `await client.get_subscriptions() -> list[dict]`: paid-plan entitlements. They include payment details, so do not log them.

iCal export feeds:

- `await client.get_ical_feeds() -> dict[str, CoziIcalFeed]`: feeds keyed by person id, with
  `FAMILY_FEED_ID` for the whole family. A feed URL is a bearer secret and is left out of `repr`.
- `await client.set_ical_feed(person_id: str, enabled: bool) -> None`: turn a feed on, or
  revoke it. Revoking breaks the URL for everyone already subscribed.

Subscribed calendars:

- `await client.get_subscribed_calendars() -> list[CoziCalendarSubscription]`: subscribed
  holiday and internet calendars, each with the members it shows for.
- `await client.subscribe_calendar(calendar_id: str, attendee_ids: Iterable[str] = ()) -> None`:
  subscribe to a calendar such as `HOLIDAY_CALENDARS["US"]` (also `CA`, `UK`, `AU`, `NEWS`),
  or change whom it shows for.
- `await client.unsubscribe_calendar(calendar_id: str) -> None`: remove a subscribed calendar.
  An internet calendar needs its URL to come back.
- `await client.add_internet_calendar(url: str, attendee_ids: Iterable[str] = ()) -> str`:
  have Cozi fetch an iCal URL as a calendar, subscribe to it, and return its id.

### Recipes

- `await client.get_recipes() -> list[CoziRecipe]`: every recipe in the box. There is no get-by-id.
- `await client.create_recipe(name: str, ingredients: str | list[str], instructions: str = "", description: str = "", notes: str = "", source: str = "", categories: list[str] | None = None) -> CoziRecipe`:
  add a recipe. `ingredients` is a list of lines or one newline-separated block; blank lines are dropped.
- `await client.update_recipe(recipe: CoziRecipe) -> CoziRecipe`: save a recipe read from Cozi
  after you assign to its attributes. The whole object is re-sent, and Cozi does not check
  versions, so the last write wins. A hand-built `CoziRecipe` is refused, because saving it
  would blank the stored recipe.
- `await client.delete_recipe(recipe_box_id: str) -> None`: delete a recipe from the box.
- `await client.import_recipe(url: str, save: bool = False) -> CoziRecipe`: scrape a recipe from
  a web page. With `save=False` you get an unsaved preview (no `recipe_box_id`); with
  `save=True`, Cozi stores it. A page Cozi cannot fetch raises `APIError`, with the reason in
  `response_data["detail"]`.
- `await client.get_curated_recipes() -> list[CoziRecipe]`: Cozi's curated recipe packs.
- `await client.save_curated_recipe(recipe: CoziRecipe) -> CoziRecipe`: copy a curated recipe
  into the box and return the copy.
- `await client.upload_recipe_photo(recipe: CoziRecipe, data: bytes, filename: str) -> CoziRecipe`:
  upload a photo (at most 20 MB) and make it the recipe's first photo. Returns the re-saved recipe.
- `await client.add_recipe_to_list(recipe: CoziRecipe, list_id: str) -> list[CoziItem]`: add a
  header with the recipe's name, then one item per ingredient. Items are added one at a time,
  so a failure part-way leaves the items already added.

### Helpers

`validate_id`, `ID_PATTERN` and `validate_calendar_period` are the checks the client runs on
ids and on `(year, month)` before they go into a URL. They are exported so that a caller (an
MCP server validating tool input, say) can apply the same rules. Other exports: `MEAL_SLOTS`,
`MAX_PHOTO_BYTES`, `FAMILY_FEED_ID`, `HOLIDAY_CALENDARS`, `default_settings`, and the models
`PersonSettings`, `NotificationChannel`, `AgendaChannel`, `EmailStatus`, `CoziSharedCalendar`
and `CoziIngredient`.

## What a caller must know

### Cozi decides from the text whether an item is a header

Cozi stores any item whose text has at least one letter, with every letter upper case, as a
section header, and ignores the `itemType` sent with it. The client works with that rule the
way the web client does:

- `header=True` sends the text in upper case.
- Plain all-caps text is sent in sentence case: `add_item(list_id, "MILK")` stores "Milk".
- Text that would still be all caps after that (`"A"`, `"X-1"`) is refused with
  `CoziValidationError`.
- `update_item_text` without `header=True` turns a header into a plain item.

Cozi also strips leading and trailing whitespace from item text and list titles, so the client
sends the text already stripped.

### update_list replaces the whole list

`update_list` sends the whole list, and Cozi makes its items match: an item you left out is
deleted, and an item without an id is created. If `lst.version` is older than Cozi's, the
write is refused with `ConflictError` (409), so read the list again just before you change
it. To change only the title, use `rename_list`.

A PUT to an id Cozi does not know creates that item or list instead of failing. When this
happens, `update_item_text`, `mark_item`, `update_list` and `rename_list` delete what was
created and raise `ResourceNotFoundError`.

### Recurring items need a scope

As shown above, a write to a series needs `Occurrence(...)` or `WHOLE_SERIES`; the web client
never makes an unscoped one. Edits replace the item whole, so always start from an item you
read. `delete_appointment` sends the item's `item_version`, and Cozi refuses a stale one: you
get `WriteVerificationError` ("version mismatch"). Read the item again after any change to it,
including a scoped write to one of its occurrences.

### date_span is a day offset

`date_span` is not a day count:

- A timed item ends `date_span` days after `start_day`, at `end_time`. 0 means the same day,
  and 1 means it runs overnight.
- An all-day item (both times `None`) ends at the midnight after its last day, so a one-day
  all-day item has `date_span` 1.

Use `appt.last_day`, `last_day_of` and `date_span_for_last_day` rather than doing the arithmetic yourself.

### Writes are verified

Cozi's calendar answers 200 even when it discards an operation, and gives the reason only in
the response body. The client checks for this and raises `WriteVerificationError`. Every create
and update returns the item as Cozi stored it, not your input echoed back.

### Paid-plan features may raise PermissionDeniedError

Search, smart add, reminders, notify and some other features need a paid Cozi plan. The client
does not gate them: if Cozi answers 402 or 403, you get `PermissionDeniedError`.

### A cookie-renewed session may outlive a password change

Like the web client, the client logs in once with the password and asks for a refresh cookie.
The token lasts four hours, and the client renews it through that cookie three minutes before
it expires, or on a 401, without using the password again. As on the web, a password change
made elsewhere does not end a session renewed this way. `logout()` does.

### One aiohttp session per account

The refresh cookie lives in the aiohttp session's cookie jar. If you pass `session=`, give each
Cozi account its own session.

### Retries

After a network error or a 5xx, GET and DELETE are retried with backoff (`retry_attempts` is
the number of attempts in all). POST, PUT and PATCH are not, because Cozi may already have
applied them. A 429 is retried for every method, and a 401 is replayed once with a renewed token.

## Exceptions

All are importable from `cozi_client`. Each carries `status_code` and `response_data`.

- `CoziException`: the base class.
  - `AuthenticationError`: login, renewal or an authenticated request was refused.
  - `CoziValidationError`: an argument was rejected before any request was sent.
  - `RateLimitError`: Cozi kept answering 429 after the retries.
  - `NetworkError`: no HTTP answer (a connection error or a timeout).
  - `APIError`: an error status, or a body that cannot be used.
    - `ResourceNotFoundError`: 404, or an id that turned out not to exist.
    - `PermissionDeniedError`: 402 or 403, often a paid-plan feature.
    - `ConflictError`: 409 or 410. Someone else changed the data; read it again and retry.
    - `WriteVerificationError`: Cozi answered with success but did not apply the write.
    - `ResponseParseError`: a success response that is not JSON, or not the expected shape.

## Excluded on purpose

The client leaves out these parts of the web client's API, because they are irreversible,
involve billing, or are of no use to a library: account deletion, Stripe checkout and payment
update or cancel, signup, Zendesk tickets, the Iterable JWT, redeeming an email-verification
token, the native-app-only refresh logout, and the Add-to-Cozi transient fetch.

## Development

```bash
git clone https://github.com/mjucius/py-cozi-client.git
cd py-cozi-client
pip install -e .[dev]
pytest              # offline: unit, model, contract and docs tests
mypy cozi_client
```

The live conformance suite (`tests/live/`) runs against a real Cozi account. It is skipped
unless credentials are set:

```bash
COZI_USERNAME=you@example.com COZI_PASSWORD=... pytest -m live
```

It changes that account, within these limits:

- Calendar writes go only to the past sandbox months 2019-01 to 2019-03, and 2022-01 for search.
- It creates and deletes throwaway lists, recipes and one family member (with no email), all
  named `LIVE_TEST_DELETE_ME:<area> <id>`. Each area sweeps its own leftovers before and after
  it runs. The household can see the test member while the suite runs.
- It renames the household and then restores the name. If a run is killed between the two
  steps, the test name stays, and the next run fails until you rename the household yourself.
- It reads iCal feeds and calendar subscriptions but does not change them. It does not touch
  password change or reset, internet calendars or the household photo.

## Examples

`examples/` has two scripts that run against your real account. They read `COZI_USERNAME` and
`COZI_PASSWORD`, or prompt for them:

- `examples/demo_lists.py` reads your lists, then works on a demo list it creates and deletes.
- `examples/demo_calendar.py` creates appointments and a weekly series in a month you pick,
  edits one occurrence, and deletes everything it created. It asks before writing.

## Requirements

- Python 3.10+
- aiohttp 3.9.2+
- pydantic 2

## Acknowledgments

- The `apikey` query parameter that Cozi requires was reverse-engineered from the live
  `my.cozi.com` web bundle by
  [Wetzel402/py-cozi PR #3](https://github.com/Wetzel402/py-cozi/pull/3). Without it, every
  login from a server environment returns 401 whether or not the credentials are valid, which
  looks exactly like a wrong password. This client would not work without that discovery. Since
  3.0 the client sends it on every request, as the web client does, not only on login.

## Trademark and affiliation

Cozi and the Cozi logo are trademarks of Cozi Group Inc. This project is unofficial and not
affiliated with, endorsed by, or sponsored by Cozi Group Inc. Use of the Cozi API is at your
own risk and subject to [Cozi's Terms of Service](https://www.cozi.com/terms-of-service/).

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Author

Matthew Jucius
