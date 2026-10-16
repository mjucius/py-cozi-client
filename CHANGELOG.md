# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-10-16

A rewrite to match the production web client at `my.cozi.com` (bundle `main.c43180f9`). The
paths, methods and bodies of the ten calls 2.x made were already right. What was wrong was the
behaviour around them, and about 45 endpoints were missing. Every endpoint, body and header now
follows what the web client sends. Where the bundle left a server behaviour open, it was
settled against a real account (see "Wire behaviour observed live" below). The package layout,
several signatures and the models change; the table below maps 2.x to 3.0.

### Breaking

| 2.x | 3.0 |
|---|---|
| `from models import ...`, `from exceptions import ...`, `from utils import ...` | `from cozi_client import ...`. The wheel is one `cozi_client` package and no longer installs top-level `models`, `utils` and `exceptions` modules. |
| `ValidationError` | `CoziValidationError` |
| `get_lists_by_type(list_type)` | `get_lists(list_type)` |
| `update_list(list_obj)` | `update_list(lst)`. It is now a full replace: items left out are deleted, and a stale `version` raises `ConflictError`. |
| `create_appointment(appointment)` | `create_appointment(appt, notify=None)` |
| `update_appointment(appointment)` | `update_appointment(appt, scope=None, notify=None)`. A recurring item needs `scope=Occurrence(...)` or `WHOLE_SERIES`. |
| `delete_appointment(appointment_id, year, month)` | `delete_appointment(appt, scope=None, notify=None)`, which takes the item read from `get_calendar` |
| `add_item(list_id, text, position=0)`, `update_item_text(list_id, item_id, text)` | New `header=False` argument. Text is sent stripped, and all-caps plain text is sent in sentence case ("MILK" becomes "Milk"), since Cozi would otherwise make it a header. A one-letter all-caps item (`"A"`) is refused. |
| `get_last_request_data()`, `get_last_response_data()` | Removed: the request data included the password. |
| `CoziClient.API_VERSION`, `CoziClient.AUTH_VERSION` | Removed. Each endpoint has its own version prefix. |
| `utils.validate_email`, `parse_time_string`, `format_time_for_display`, `calculate_date_span`, `split_long_text`, `sanitize_list_title`, `sanitize_item_text`, `extract_attendee_names`, `is_weekend`, `get_week_boundaries` | Removed. For `calculate_date_span`, use `date_span_for_last_day(start, last_day, all_day)`. The old one counted days inclusively, which is wrong for `dateSpan`. |
| `ListType`, `ItemStatus`: plain `Enum` with `use_enum_values` | `(str, Enum)`. `lst.list_type` and `item.status` are now enum members. They still compare equal to `"shopping"` and so on, but on Python 3.11+ an f-string renders them as `ListType.SHOPPING`, so use `.value`. Methods accept the enum or the string. |
| `CoziItem.position`, `due_date`, `notes`, `owner`, `created_at`, `updated_at`; `CoziList.owner`, `notes`, `created_at`, `updated_at` | Removed as fields. Cozi never sends `position`, `dueDate` or the timestamps. `notes` and `owner` are kept as pydantic extras and written back unchanged. |
| `CoziAppointment.to_api_create_format()`, `to_api_edit_format()`, `to_api_delete_format()`, `start_date`, `notes_html`, `notes_plain`, `description_short`, `household_member`, `created_at`, `updated_at` | Removed. The client builds the request bodies, and unknown wire keys are kept as extras. |
| `CoziAppointment.end_day` (a `str` field) | A read-only `date` property, read from `recurrence.endDay` (the last day of the series) |
| `CoziAppointment.recurrence_start_day: str` | `date` |
| `CoziPerson.settings: dict`, `email_status: str` | `PersonSettings`, and `EmailStatus` (or a plain `str` for values Cozi adds later) |
| Python 3.7+ | Python 3.10+ |

### Fixed

- **All-day writes failed.** They sent `null` times, which Cozi refuses ("start_time and
  end_time are required"), so creating an all-day item failed, and so did editing any all-day
  item read by `get_calendar`. They now send `"00:00"`/`"00:00"` with a `date_span` of at least 1.
- **`update_list` always raised `AttributeError`** (`.value` on a `use_enum_values` string).
  It also sent items keyed `id` instead of `itemId`, dropped `itemType`, and invented
  `externalIds` and `notes`. It now sends the list as read, with the caller's changes.
- **`create_appointment` silently dropped `recurrence`.**
- **Birthdays, meals and subscribed-calendar items were read as appointments.**
  `get_calendar` forwarded eight keys and dropped `itemType`, `itemSource`, `itemVersion` and
  `reminders`, so an edit overwrote these items as appointments.
- **`end_day` was always empty.** It read a top-level key that never exists, and its validator
  returned a `date` into a `str` field.
- **A 00:00–01:00 event lost its start time,** because each `00:00:00` was nulled on its own.
  An item is now all-day only when both times are midnight.
- **A calendar item with a bad or missing `day` silently became today.** It is now skipped,
  with a warning.
- **`calculate_date_span` returned `max(1, days + 1)`,** an inclusive count, but `dateSpan` is
  a day offset.
- **The wheel shipped top-level modules named `models`, `utils` and `exceptions`, and no
  package `__init__.py`.**
- **`get_last_request_data()` exposed the password.**
- **Timeouts, pydantic validation errors and non-JSON 2xx bodies escaped `CoziException`.**
  Timeouts now raise `NetworkError`, and the other two raise `ResponseParseError`.

### Changed

- **The `apikey` goes on every request**, not only the login, as the web client sends it
  (`coziwc|v265_production`).
- **Tokens are renewed as the web client renews them.** The login asks for a refresh cookie.
  The token is renewed through that cookie (`/2111/{acct}/auth/reauth`, no password) 180 s
  before it expires and on a 401, falling back to a password login if the refresh is refused.
- **Logins and renewals are single-flight.** Concurrent calls under `asyncio.gather` share one
  login.
- **`logout()` ends the session on the server** (`/2111/{acct}/auth/logout`, which revokes the
  refresh cookie), then clears the token and cookies. 2.x claimed no such endpoint existed.
- **Creates and updates return what Cozi stored,** not the input echoed back. A new calendar
  item's id is the one id on the answering month page that was not there before the write. This
  replaces the first-match and subject-only fallbacks, which could return an existing appointment.
- **`get_calendar` reads the page's `days` map.** Each item carries `occurrence_days`, its
  occurrences that month, and a multi-day item is listed under its start day.
- **A write to a recurring series needs a scope.** 2.x sent unscoped writes, which hit every
  occurrence; the web client never does that.
- **More statuses are mapped:** 409 and 410 raise `ConflictError`, and 402 raises
  `PermissionDeniedError`. An error's message includes Cozi's `detail` when it sends one.
- **Timeouts and retries.** The timeout applies per attempt, including on an injected session.
  A 401 replay no longer uses up a retry. A DELETE retried after a 5xx that then gets 404 counts
  as success. Requests stay at least 100 ms apart, under a lock.
- **The 201-upsert guard covers list writes too.** `update_list` and `rename_list` now refuse
  to create a list, as `update_item_text` and `mark_item` already refused to create an item.
- **`remove_items` is stricter.** A response that is not a list now raises
  `ResponseParseError`, where 2.x returned `True`. Surviving ids are matched
  case-insensitively, as Cozi matches them. A bare string for `item_ids` is refused, where 2.x
  iterated it one character at a time.

### Added

- **Auth:** `change_password`, `request_password_reset`, `submit_password_reset`, and the `account_id` property.
- **Lists:** `rename_list`, `reorder_list`, `delete_item`, `get_lists(list_type)`, `header=` on
  `add_item` and `update_item_text`, and `CoziItem.is_header`.
- **Calendar:**
  - Series scopes: `Occurrence`, `Extent`, `WHOLE_SERIES` and `Scope`.
  - Helpers: `repeat_rule`, `last_day_of` and `date_span_for_last_day`.
  - `notify=` and reminders on appointments.
  - `create_birthday`; `create_meal`, `update_meal`, `delete_meal` and `MEAL_SLOTS`.
  - `search_calendar`, `get_calendar_print` and `parse_event` (smart add).
  - `CoziAppointment` fields: `item_type`, `item_source`, `item_version`, `reminders`,
    `occurrence_days`, `name`, `birth_year`, `meal_slot` and `meal_items`.
  - `CoziAppointment` properties: `is_all_day`, `is_recurring`, `skipped_days`,
    `recurrence_text`, `end_day`, `last_day` and `is_editable`.
- **Account:**
  - Members: `create_person`, `update_person`, `delete_person` and `send_verification_email`.
  - Household: `get_household`, `update_household` and `upload_household_photo`.
  - Reads: `get_config`, `get_personal_info` and `get_subscriptions`.
  - iCal feeds: `get_ical_feeds` and `set_ical_feed`.
  - Subscribed calendars: `get_subscribed_calendars`, `subscribe_calendar`,
    `unsubscribe_calendar` and `add_internet_calendar`.
  - Models and constants: `PersonSettings`, `NotificationChannel`, `AgendaChannel`,
    `EmailStatus`, `default_settings`, `CoziHousehold`, `CoziIcalFeed`, `CoziSharedCalendar`,
    `CoziCalendarSubscription`, `FAMILY_FEED_ID` and `HOLIDAY_CALENDARS`.
- **Recipes:** `get_recipes`, `create_recipe`, `update_recipe`, `delete_recipe`,
  `import_recipe`, `get_curated_recipes`, `save_curated_recipe`, `upload_recipe_photo` and
  `add_recipe_to_list`, with `CoziRecipe`, `CoziIngredient` and `MAX_PHOTO_BYTES`.
- **Exceptions:** `ConflictError` and `ResponseParseError`.
- **Packaging:** a `py.typed` marker, and `__version__` read from the package metadata.
- **Tests:** a contract suite that drives the client over a real socket against a strict stub
  of `rest.cozi.com`, and a live conformance suite (`pytest -m live`).

### Wire behaviour observed live

Seen on a real account on 2026-10-16. The client relies on each of these.

- **Lists**
  - Cozi decides from the text alone whether an item is a header: any text with at least one
    letter, all of them upper case. It ignores the `itemType` sent, and `"2024"` is never a header.
  - Cozi strips leading and trailing whitespace from item text and list titles.
  - A full-list PUT replaces the items: one left out is deleted, and one without an id is
    created. A stale `version` gets 409. A title-only PUT leaves the items alone.
  - A PUT to an unknown uuid creates the item (201), and so does a PUT to a list when it
    carries a `listType`. A PUT to an id that is not a uuid gets 404.
  - Ids are matched case-insensitively.
  - Deleting an item answers 204 for any uuid, even one that is not on the list.
  - `?list_type=` filters on the server.
  - The `reorder_list` index counts within the list's own type.
- **Calendar**
  - A write answers 200 with the whole month page. An operation Cozi refuses is reported only
    in `rejectedItems`.
  - A stale `delete.version` comes back that way too ("version mismatch"), not as a 409 or 410.
  - Sending `itemVersion` on an edit makes Cozi discard the edit (seen 2026-07-24, and the
    reason 2.1.0 stopped sending it).
  - `dateSpan` is a day offset. An all-day item is stored as `00:00:00`/`00:00:00` with a
    `dateSpan` of at least 1.
  - On a later month's page, a series keeps its original start `day`; only `days` names that
    month's occurrences.
  - An `extent: "day"` edit stores a stand-in under a new id, linked from the series by an
    exdate's `standinApptId`.
  - An `extent: "forward"` edit from a later occurrence ends the old series and starts a new
    one. From the first occurrence, it edits the series in place.
  - A birthday without a birth year is stored as starting in 1904.
  - Smart add mints an id that names nothing stored.
  - The print view is a PDF.
  - Search keys its hits `<uuid>-a`.
- **Auth**
  - A token lasts 14400 s.
  - Reauth with the refresh cookie needs no password, and returns the same token while that
    token is still valid.
  - Logout revokes the cookie.
- **Account**
  - Person and household writes answer with the stored record.
  - A person's `calendarItemNotification` also carries a `sendByDefault` flag.
  - A member without an email has `emailStatus` `"not verified"`, and an empty email is stored as `null`.
- **Recipes**
  - A POST of a whole recipe that carries its `recipeBoxId` updates it in place (PUT does too).
  - `version` is not checked: the last write wins.
  - `recipeSource` and `title` are accepted but not stored.
  - A scrape Cozi cannot fetch is a 400 with the reason in `detail`. A page with no recipe is a
    200 with no ingredients.
  - Uploading a photo does not attach it to the recipe; saving the recipe does.

## [2.1.0] - 2026-08-14

Ports the security and write-correctness fixes from the parallel TypeScript
client in `cozi_mcp` (commit `329f8a6`), where each server behavior described
below was proven against a live Cozi account. Also closes two path-traversal
sinks that exist only in this client, in `get_calendar` and
`delete_appointment`.

### Added

- **`WriteVerificationError`**, raised when Cozi returns a success status for a
  write it did not apply. See the `rejectedItems` entry below.
- **`validate_id`, `ID_PATTERN`, and `validate_calendar_period`** are now
  exported from the package. `ID_PATTERN` is kept byte-identical to `ID_RE` in
  `cozi_mcp` so both clients agree on what a legal id is, and so a downstream
  caller — an MCP server validating LLM-supplied ids at its own tool boundary,
  say — can reuse the pattern rather than re-deriving it.

### Fixed

- **Editing a recurring appointment no longer destroys the series.** Cozi's
  calendar edit is a full replace, and `CoziAppointment.to_api_edit_format()`
  never sent `recurrence`, so changing anything else (notes, time, subject) on a
  recurring appointment silently flattened it to a single event. The rule is now
  re-sent at the edit level, as a sibling of `details` — a calendar GET returns
  it nested under `itemDetails`, but an edit expects it one level up, and nesting
  it inside `details` leaves the rule unset. `endDay` lives inside the recurrence
  object and so round-trips with it. `itemVersion` is still never sent: including
  it makes the server discard the entire edit while returning 200.

- **Writes that Cozi silently discards now raise** instead of reporting success.
  The calendar endpoint answers HTTP 200 while refusing an operation, naming the
  reason only in a `rejectedItems` array that `create_appointment`,
  `update_appointment`, and `delete_appointment` all ignored. Adds the new
  `WriteVerificationError`, which is also raised when `add_item`,
  `update_item_text`, `mark_item`, or `remove_items` get back a response that
  does not reflect what was asked for.

- **A stale item id no longer creates a phantom item.** A `PUT` to an item id
  that does not exist answers **201** and persists a brand-new item under that
  exact id — an upsert, not an error. `update_item_text` and `mark_item` had no
  way to see this because 200 and 201 were collapsed into one path and the bodies
  are identical. They now raise `ResourceNotFoundError` and delete the phantom
  first; if that cleanup fails the original error still surfaces, noting that the
  item may remain.

- **Failed writes are no longer replayed.** Retries after a network error or 5xx
  were applied to every method, so a `POST`/`PUT`/`PATCH` that Cozi had already
  applied server-side could be double-applied — duplicate appointments or items.
  Retries are now scoped to idempotent methods; 401 and 429 still retry for all
  methods, since both are rejections issued before the request was applied.

### Security

- **Path traversal via list and item ids (CWE-22).** A `list_id` or `item_id`
  containing `../` escaped the account-scoped URL prefix: `urljoin` applies RFC
  3986 dot-segment removal and aiohttp's yarl normalizes again, so
  `/api/ext/2004/<acct>/list/../../../../evil` resolved to
  `rest.cozi.com/api/evil` — aiming a request that carries the account's bearer
  token at an arbitrary path on the host. A `?` or `#` instead truncated the
  path into a query string or fragment. In `remove_items` the same ids were
  interpolated into a JSON-Pointer `path` with no RFC 6901 escaping, where a `/`
  or `~` retargeted the patch at a different node. Ids are now restricted to
  `[A-Za-z0-9_-]` by the new `validate_id`, applied at method entry — before any
  authentication or network I/O, so a malformed id costs no round trip. Affects
  `update_list`, `delete_list`, `add_item`, `update_item_text`, `mark_item`, and
  `remove_items`. Ports VULN-001/002 from `cozi_mcp` `329f8a6`.

- **`get_calendar` and `delete_appointment` now validate `year` and `month`.**
  Both interpolate the caller's values straight into `/calendar/{year}/{month}`,
  and the `int` annotations are not enforced at runtime — `get_calendar` bounded
  only `month`, and `delete_appointment` bounded neither, so a string `year` was
  the same traversal in a different path. Both now go through
  `validate_calendar_period`. No TypeScript counterpart; found while porting the
  above.

- **Access and refresh tokens no longer leak into errors or logs.** A failed
  login raised `AuthenticationError` with the entire response body interpolated
  into the message, and `authenticate()` unconditionally debug-logged the full
  response. The error now names only the missing field, and the log records only
  the response's keys.

- Re-authentication after a mid-session 401 replaced the request headers instead
  of updating them, dropping the browser headers Cloudflare requires — so the
  retry would 401 again. It now preserves them.

## [2.0.4] - 2026-05-10

### Changed

- **Bumped `softprops/action-gh-release` from `@v2` to `@v3`** in the release
  workflow. The v2 tag still runs on Node 20, so the 2.0.3 run emitted one
  residual Node 20 deprecation warning for this action; v3 moves it onto
  Node 24. No runtime behavior changed.

## [2.0.3] - 2026-05-10

### Changed

- **Modernized the release workflow** (`.github/workflows/release.yml`) to
  silence GitHub-emitted deprecation warnings. Bumped `actions/checkout` to
  `@v5` and `actions/setup-python` to `@v6` so the workflow runs on Node 24
  ahead of the June 2, 2026 forced cutover. Replaced the archived
  `actions/create-release@v1` and `actions/upload-release-asset@v1` (which
  also triggered repeated `set-output` deprecation warnings) with a single
  `softprops/action-gh-release@v2` step that both creates the release and
  attaches the wheel and source distribution. No runtime behavior changed;
  this release exists to validate the new pipeline end-to-end.

## [2.0.2] - 2026-05-10

### Fixed

- **Appointment notes now round-trip from `get_calendar`.** Cozi returns
  appointment notes inside `itemDetails.notes`, not at the top level of the
  calendar item payload. `_parse_calendar_item` was synthesizing a new
  `itemDetails` containing only `location`, which silently dropped the notes
  field before `CoziAppointment.extract_item_details` had a chance to hoist
  it. Now the full `itemDetails` dict is preserved so the model validator can
  hoist `notes`, `location`, and any other nested fields. Discovered while
  smoke-testing the v2.0.1 auth fix against real appointment data.

## [2.0.1] - 2026-05-10

### Fixed

- **Authentication restored.** Cozi changed the `/api/ext/2207/auth/login`
  endpoint sometime in early 2026 to require an `apikey` query parameter and
  browser-shaped request headers (Origin, Referer, User-Agent). Without these,
  every login attempt returned `401 Unauthorized` with the misleading message
  "your browser does not understand how to supply the credentials required",
  regardless of credential validity. The client now sends
  `?apikey=coziwc|v251_production` on the login request and includes
  browser-impersonation headers on every request. Discovered via
  [Wetzel402/py-cozi PR #3](https://github.com/Wetzel402/py-cozi/pull/3) and
  confirmed against the live `my.cozi.com` JS bundle. Any
  `coziwc|vNNN_production` value is accepted; the live web client currently
  ships `v257_production`.

## [2.0.0] - 2026-04 (unreleased — superseded by 2.0.1)

### Changed

- Migrated to Pydantic v2.
- Added automated test suite.
- Fixed several appointment-parsing bugs.
