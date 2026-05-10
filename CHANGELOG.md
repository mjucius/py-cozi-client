# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
