# Changelog

Release notes for GitHub Releases are sourced from this file. Each released version
must have a matching `## [x.y.z]` section before merging to `main`.

## [0.7.5] - 2026-09-20

### Fixed

- Wealthsimple scrapes work again after WS rebuilt the activity page. The CSV export failed on one
  thing only -- the "All accounts" master checkbox, whose label WS moved out of the control -- and
  because the scraper treated that checkbox as required, a working export died there and fell back
  to reading the activity feed, which the same rebuild had also broken. The checkbox is now found
  through a chain of locators and treated as optional: when none of them matches, the per-account
  rows are ticked instead, which reaches the same place.
- The activity feed is read again. WS moved it onto Base UI accordions, which leave a collapsed row
  with no id and no `aria-controls` at all, so every selector the fallback used to find, expand and
  harvest rows addressed markup that no longer exists -- and the attribute it read each field by
  now marks only the status badge. Rows are read from their collapsed headers in a single pass
  instead, which the rebuilt header makes possible by carrying payee, type, account, amount and
  status itself. Nothing is expanded, so there is no region id left to go stale.
- A scrape that fails both ways now says so. Only the fallback's error reached the browser, so a
  failed export was reported as "Couldn't find any activity header buttons" -- a message about the
  wrong half of the scrape. Both causes are now named in one error, and the export's "Download
  activities" step saves a failure screenshot like every other step.
- An activity page with no rows is no longer a failure. A window you had no activity in returns an
  empty result rather than a timeout.
- The activity feed is waited for, not just the page around it. The shell renders while the feed
  behind it is still being fetched, so reading it straight away returned an empty CSV from an
  account that had plenty of activity. An empty feed is still not an error -- the two are told
  apart in the log and a screenshot.
- An export whose account rows this scraper cannot recognise no longer fails. WS's account rows
  lost the `data-testid` the selection was read back through, which left a correctly set-up
  export failing its own verification; the export now proceeds on the "All accounts" click, and
  the downloads that arrive are served.

### Changed

- The activity-feed fallback no longer reports trade quantity, limit price, exchange rate, or the
  exact filled and submitted times. Those live in a row's expanded panel, which is no longer
  opened. The fallback runs only when the CSV export is unavailable, and the export carries the
  full detail; trades take their date from the day header, so they still reach the output.

## [0.7.4] - 2026-08-24

### Fixed

- BMO's two-factor step works again on the rebuilt OTP screens. BMO replaced them with an Angular
  micro-frontend that stamps a fresh UUID on every element and dropped the NEXT interstitial the
  scraper waited for, so the phone option was never selected and no code was ever sent. Each step
  is now found through a chain of locators — the stable `name` attribute first, then the ARIA
  structure, then the visible label text — and every click is read back from the page: the phone
  option is chosen by what it says rather than by its position, and a click that selected the wrong
  method or left the confirmation box unticked moves on to the next locator instead of quietly
  requesting a code that never arrives.
- "Trust this device" is never ticked. The box is only read back, and unticked again if a build
  ever renders it pre-selected, so scraping no longer risks registering the machine with BMO.
- A click the cookie consent banner swallowed no longer stalls the 2FA screen. The banner is
  cleared off that screen when it reappears there, and a click the page shows didn't land is
  retried through the DOM, which reaches a control something invisible is sitting on top of.
- A rejected verification code now fails naming BMO's own inline error ("Please enter a valid
  code.") instead of only the elapsed timeout, and the CONTINUE step that current builds no longer
  show is clicked only when it is actually there.

## [0.7.3] - 2026-08-23

### Fixed

- BMO sign-in no longer stalls on the login page. BMO dropped the `aria-label` the scraper keyed
  on and gives the button a fresh random `id` on every render, so the click never landed. The
  button is now found by the form's stable `name="login-submit"`, with the old label and the
  visible "Sign in" text tried in turn if that markup changes again.

## [0.7.2] - 2026-08-07

### Added

- `POST /ws/scrape` takes an optional `account_ids`, and every account named gets a day-by-day
  earnings CSV alongside the activity export. A day's change is split into the money that moved
  in or out and what the account earned once that movement is taken back out — market moves,
  dividends, interest, fees — so a deposit no longer reads as growth. 
- Each half of a day is written as its own line, told apart by an `entry_type` column (`deposit`
  or `earning`) alongside a single `amount`, which is the shape a ledger reads. A day where money
  went in *and* the market moved produces two lines sharing one date; a half that came to zero
  writes no line, so most days carry an earnings line alone and a day where nothing moved
  contributes nothing.

## [0.7.1] - 2026-08-04

### Fixed
- Wealthsimple missing import.

## [0.7.0] - 2026-08-02

### Added

- Wealthsimple activity is now collected through WS's own "Download activities" CSV export
  instead of by expanding every row of the activity feed. 
- Browser downloads are now supported by the zendriver engine: `_set_download_directory` routes
  them into a scrape-owned directory and `_wait_for_downloads` waits for the whole batch,
  treating the download as complete only once no `.crdownload` remains and the finished set has
  stopped changing.
- `/scrape` responses now log their delivery: status, total bytes written and elapsed time on
  success, and a warning when the body is cut short.
- `POST /client-log` records browser-side failures in the server log, next to the scrape they
  belong to. A message shown only in the page is gone as soon as the user navigates away.

### Changed

- A multi-file scrape is no longer packed into a ZIP, uses multi download instead.
- The Wealthsimple page's "Download by Account" dropdown is no longer necessary, downloading all accounts.
  filter rows client-side — mangling any quoted field containing a line break — while
  Wealthsimple already exports one file per account.
- A multi-account BMO scrape now serves one file per account instead of a ZIP, through the same
  mechanism as Wealthsimple
- A Wealthsimple export that carries an `account_type` column is now split along it: each account
  type gets its own CSV, prefixed with the type lowercased and with spaces as dashes
  (`Credit Card` -> `credit-card-activities-export.csv`). 

## [0.6.6] - 2026-07-27

### Fixed

- Wealthsimple activity scraping no longer returns empty rows after a WS front-end redeploy.
  `parse_region_html` located each field's value by a hardcoded styled-component hash class
  (`gQehiP`), which WS regenerates on every deploy.

## [0.6.5] - 2026-07-21

### Fixed

- BMO transaction scraping (`csv_web`/`from_date` path) no longer times out for 60 s and fails when
  a UUID belongs to a bank (chequing/savings) account. The scraper hardcoded the `/cc/` credit-card
  URL prefix, so a `/ba/` account rendered no transaction table. Each UUID is now resolved to its
  real account URL from the account side nav (one lookup per session covers every account), and the
  CSV/QFX download path rejects bank accounts up front with a clear message instead of an opaque API
  error.
- BMO web-parsing now scopes every transaction/pagination locator to the visible Ionic page. BMO's
  router keeps previously visited account pages mounted (hidden) in the DOM, so document-wide
  locators were picking up rows and buttons from other accounts, corrupting the parse and the
  "did the page advance?" check.
- BMO web-parsing reads the transaction table's column layout from its header row, so both the
  credit-card layout (`Money in/out`) and the bank layout (`Money out`/`Money in`/`Balance`) parse
  correctly; a bank "money in" row no longer aborts the scrape with `Invalid amount`.
- BMO pagination can no longer loop forever. It now uses the `1-20 of N` range label to detect the
  end of the list, raises if a Next click fails to advance the pager, and is bounded by a hard
  page cap. It also switches the page-size selector to its largest option to walk fewer pages.

## [0.6.4] - 2026-07-17

### Fixed

- Wealthsimple credit-card transactions no longer go missing from the scrape. The activity
  feed moved its day-date headers from `<h2>` to `<h3>`, so every row lost its date and
  credit-card purchases — which carry no date inside their own detail region — were silently
  dropped. The scraper now reads the `<h3>` headers (including the relative "Today"/"Yesterday"
  labels) and threads each row's day-header date through as a fallback `Date`.

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
