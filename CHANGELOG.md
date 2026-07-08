# Changelog

Release notes for GitHub Releases are sourced from this file. Each released version
must have a matching `## [x.y.z]` section before merging to `main`.

## [0.6.3] - 2026-07-08

### Added

- BMO can now scrape multiple credit card accounts in a single session: pass a list of
  `account_uuids` to `/bmo/login` (a single string is still accepted). When more than one account
  is scraped, `/bmo/scrape` returns a ZIP archive with one transaction file per account, each
  namespaced under its account UUID. The BMO page exposes a multi-line UUID field for this.

## [0.6.2] - 2026-07-08

### Fixed

- Browser sessions no longer crash on login with `PermissionError` when the container's
  Chrome user-data directory is owned by a different account: the Dockerfile now assigns
  it to the base image's `seluser` by name, and the scraper falls back to the system temp
  directory if the configured parent is unwritable.

## [0.6.1] - 2026-07-02

### Changed

- Migrated the Wealthsimple, RBC, and Scotiabank scrapers to the zendriver (CDP) engine; all scrapers now share one browser engine.
- Rewrote the Wealthsimple activity scrape: removed the global 20s implicit wait, rows are expanded by stable `aria-controls` ids and harvested in a single batch DOM pass, eliminating the long idle stalls.
- Field readback is now best-effort: if a typed value can't be verified after its retries, the scraper logs a warning and still tries to connect instead of aborting the login — masked inputs never report their value even when the characters landed correctly.
- Wealthsimple OTP entry no longer reads back or clears/re-types the code; it types once and submits, since the one-time-code field masks its value.

### Removed

- The legacy Selenium browser engine and the `selenium`/`browserforge` dependencies.

## [0.6.0] - 2026-07-01

### Changed

- Made browser sessions visible for bank scraper flows.
- Moved browser automation toward Zendriver.
- Continued fixes for the visible-browser migration.

## [0.5.17] - 2026-06-22

### Changed

- Updated web drivers.
- Fixed BMO scraping behavior.

## [0.5.16] - 2026-06-17

### Fixed

- Reinforced BMO password entry behavior.

## [0.5.15] - 2026-06-11

### Added

- Added optional HTML capture on scraper failure.

## [0.5.14] - 2026-06-11

### Changed

- Made scraper logs and error messages more verbose.

## [0.5.13] - 2026-06-05

### Changed

- Switched project dependency and workflow management to uv.

## [0.5.12] - 2026-06-05

### Added

- Added GitHub workflow support for pushing Docker images.

## [0.5.11] - 2026-06-05

### Added

- Show a notice when a newer PennySpy version is available.

## [0.5.10] - 2026-06-05

### Added

- Show the current PennySpy version in the web UI.

[0.6.0]: https://github.com/moqba/PennySpy/compare/v0.5.17...v0.6.0
[0.5.17]: https://github.com/moqba/PennySpy/compare/v0.5.16...v0.5.17
[0.5.16]: https://github.com/moqba/PennySpy/compare/v0.5.15...v0.5.16
[0.5.15]: https://github.com/moqba/PennySpy/compare/v0.5.14...v0.5.15
[0.5.14]: https://github.com/moqba/PennySpy/compare/v0.5.13...v0.5.14
[0.5.13]: https://github.com/moqba/PennySpy/compare/v0.5.12...v0.5.13
[0.5.12]: https://github.com/moqba/PennySpy/compare/v0.5.11...v0.5.12
[0.5.11]: https://github.com/moqba/PennySpy/compare/v0.5.10...v0.5.11
[0.5.10]: https://github.com/moqba/PennySpy/releases/tag/v0.5.10
