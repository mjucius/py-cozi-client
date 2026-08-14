# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
