# Wealthsimple (WS) scraping flow

How the Wealthsimple scraper gets from the first page load to the CSVs it returns. Selectors are
the ones in `connection_element_id.py` and `activity_id.py` as of October 2026. WS's front end
is styled-components, and the class names are hashes that change on every deploy, so the
selectors key on `data-testid`, `data-qa`, `data-fullstory`, ARIA and visible English text.

Domain involved:

| URL | Used for |
|---|---|
| `my.wealthsimple.com/login` | Entry point. Redirects to `/app/login` |
| `my.wealthsimple.com/app/...` | Sign-in steps (`login`, `verify`, `2fa`, `mfa`, ...) and the signed-in app |
| `my.wealthsimple.com/app/activity` | Activity feed and the "Download activities" export |
| `my.wealthsimple.com/app/account-details/<account_id>` | An account's value chart (for daily earnings) |
| `my.wealthsimple.com/graphql` | The app's GraphQL API, queried for the account graph |

Sign-in pages and the app share `/app/`, so a URL alone doesn't prove the session is signed
in. `is_signed_in_url()` treats a URL as signed in when it's under `/app/` and its first segment
isn't a sign-in step. `_is_logged_in()` also requires that none of the sign-in controls
(password field, code field, passkey challenge) is on screen.

The flow is split in two because a person has to read the code. **Login** runs steps 1 to 3a
and stops at the code prompt. **Verify + scrape** runs steps 3b to 6 once the code arrives.

---

## 1. Open the login page

1. Navigate to `https://my.wealthsimple.com/login`. It redirects to `/app/login`.
2. Set the implicit wait to `PAGE_LOADING` (20 s), so element lookups give the page time to
   render.

There's no cookie banner to dismiss on this flow.

## 2. Sign in

| Field | Locator | Notes |
|---|---|---|
| Email | `//input[(@type="email" or @type="text") and @inputmode="email"]` | The October 2026 page dropped the aria-label; this matches both the old and new form |
| Password | `//input[@type="password"]` | |
| Log in | `//button[@type="submit"]` | Not the "Log in with a passkey" button (`type="button"`) |

Each field is typed with `_send_keys_verified` (read back and retried up to 3 times if an
overlay steals focus) and logged as redacted. The credentials come from the saved login
(`LoginCredentials`), or else from `PENNYSPY_WSU` / `PENNYSPY_WSP`. `.reveal()` is only called
while typing them.

Wrong login: for `LOGIN_ATTEMPT` (5 s) after submit, the scraper looks for
`//p[contains(text(), "Your email or password was incorrect")]`. If it shows up, the login
fails with "Username and password seems to be invalid".

## 3. Two-factor code

### 3a. Get to the code field

Several things can follow the password. The scraper waits up to `PASSKEY_CHALLENGE` (3 min)
for the first of them to appear, checked in this order:

| What shows up | Locator | What happens |
|---|---|---|
| SMS code field | `//input[@data-testid="otp-input" or (@type="text" and @autocomplete="one-time-code")]` | Stop and ask for the code |
| "Check your trusted device" | `//h1[normalize-space()="Check your trusted device"]` | Return `waiting_for_external` with "…tap Yes to approve this sign-in. Waiting for your approval…" (see 3c) |
| Passkey challenge, "Use your passkey to verify that it's you" | `//button[@data-testid="passkey-challenge-2fa-error-try-another-way-button"]`, then `//button[.//span[normalize-space()="Try another way"]]` | Click **Try another way**, which sends the SMS code, then wait up to `PAGE_TIMEOUT` (60 s) for the code field |
| Signed-in app | `_is_logged_in()` | No code needed; return `authenticated` |

The code field is checked first so a page that already shows it is never mistaken for the
challenge. The trusted-device page comes next: it has a **Try another way** button of its own,
which the passkey challenge's label locator would otherwise click. The challenge only appears for an account with a passkey, and only after Chrome's
own passkey request has timed out unanswered, which takes a minute or more. That's why the
wait is so long.

If none of them shows up, the scraper logs a warning and still returns `needs_otp`; step 3b
reports the missing field. The message is fixed: "Enter the OTP code sent to your phone".

Login stops here and keeps the browser session open for verify.

### 3b. Submit the code

1. If the session is already signed in (WS skipped the code, or someone typed it in the noVNC
   window), return `authenticated` without touching the page.
2. Type the code into the code field (redacted in logs). There's no read-back, because the
   field masks its value and clearing and retyping can auto-submit.
3. WS usually submits the code itself once the last digit is in, and its submit button is
   gone by then. So the scraper waits `OTP_AUTO_SUBMIT` (15 s) for an outcome first. Only if
   the page is still sitting on the code does it click `//button[@type="submit"]`, so an
   auto-submitted code is never sent twice. It then waits up to `OTP_OUTCOME` (2 min).
4. Outcomes:

   | Outcome | Locator | What happens |
   |---|---|---|
   | Wrong code | `//div[@role="alert" and contains(text(), "Try again or get a new code")]` | Fail with "OTP code didn't work". The session is closed, so login has to start again |
   | "Check your trusted device" | `//h1[normalize-space()="Check your trusted device"]` | Return `waiting_for_external` (see 3c) |
   | "Create passkey" offer | `//button[.//span[normalize-space()="Maybe later"]]` | Click **Maybe later** |
   | Signed in | `_is_logged_in()` | Done |

5. Dismiss the "Create passkey" offer if it appears late (optional check, 5 s), then confirm
   the app is signed in (up to 2 min).

### 3c. Approve on the trusted device

When the login comes from a new place, WS can show "Check your trusted device", right after
the password or after the SMS code. It has sent a notification to the Wealthsimple app on the
user's phone and waits for them to tap **Yes**. There's nothing to type, so:

1. The login (or verify) answers `status: "waiting_for_external"` with the message
   "Wealthsimple sent a notification to your trusted device. Open the Wealthsimple app on your
   phone and tap Yes to approve this sign-in. Waiting for your approval…".
2. The web UI shows that message on its spinner, with how long it has been waiting, and calls
   `/ws/verify` with no code straight away. Nothing has to be clicked.
3. That verify waits up to `DEVICE_APPROVAL` (5 min) for one of:

   | Outcome | How it's seen | What happens |
   |---|---|---|
   | Approved | The signed-in app, or the "Create passkey" offer in front of it | Dismiss the offer if shown, confirm the app is signed in, return `authenticated` |
   | "Try another way" chosen (in the app, or on the page through noVNC) | The SMS code field | Return `needs_otp`; the web UI shows the code field |
   | Nothing within 5 minutes | | Fail with "The sign-in was not approved on the trusted device within 5 minutes; start the login again" |

The scraper never clicks **Resend notification** or **Try another way** on this page.

## 4. Pick the export window

The web UI sends a `since_date`. WS's export only offers fixed windows, so the scraper picks
the shortest one that still reaches back to it (`select_export_period`):

| Window | Chosen when `since_date` is on or after |
|---|---|
| Last 3 months | today − 3 months |
| Last 6 months | today − 6 months |
| Last 12 months | today − 12 months, or anything older (logged as a warning) |

The web UI's dropdown offers these same three windows, so `since_date` normally lines up with
one exactly. The export covers the whole window and isn't trimmed to `since_date`.

## 5. Download the activity export

On `https://my.wealthsimple.com/app/activity`. Every control in the dialog has a
`data-testid`:

1. Wait for **Download activities**: `button[data-testid="button-download-activities"]`
   (`PAGE_LOADING`, 20 s). Point the browser's downloads at a private `ws_export/`
   subfolder of the export directory, then click the button.
2. **Period**: if `button[data-testid="activities-export-period-selector"]` doesn't already
   show the chosen window, open it and click the option, matched by its label. The first try
   is `//*[(@role="option" or @role="menuitem" or self::li) and normalize-space()="<label>"]`,
   then `//*[@role="listbox"]//*[normalize-space()="<label>"]`. If the selector still shows
   something else afterwards, that's logged and the export goes on.
3. **Next**: `button[data-testid="button-activities-export-next"]`.
4. **Accounts**: tick every account.
   - The quick way is the "All accounts" checkbox, tried through three locators: the
     checkbox wrapping its label (pre-redesign), the one with `aria-label="All accounts"`, and
     a checkbox in the same row as the "All accounts" text (the September 2026 layout).
   - The account rows are `button[data-testid="generate-documents-account-row"]`, ticked when
     `aria-checked="true"`. An account still being opened renders as a `div` with the same
     testid and is skipped.
   - If there's no "All accounts" checkbox, or it leaves rows unticked, each row is clicked on
     its own. A row that can't be ticked fails the export.
   - The number of rows ticked is how many files to expect. If the rows can't be read at all,
     the export goes ahead on the "All accounts" click with no expected count.
5. **Download CSV**: `button[data-testid="generate-documents-cta-button"]`, once enabled.
6. **Wait for the files.** WS builds each account's CSV on the server, one at a time, so they
   trickle in over several minutes, with gaps where nothing is downloading at all. The wait
   (`_wait_for_downloads`) is done when no partial `.crdownload` file is left and the set of
   finished files has stayed the same for:
   - `DOWNLOAD_SETTLE` (8 s) once as many files as accounts have arrived;
   - `DOWNLOAD_INCOMPLETE_SETTLE` (60 s) while fewer have.

   The overall limit is `DOWNLOAD_TIMEOUT` (5 min). Files that did arrive are returned even if
   the batch never settled. A count that doesn't match the accounts is logged as a warning;
   WS sometimes answers with one combined CSV.

### Split by account type

The CSVs are served as WS wrote them: no column mapping and no row filtering. The one change
is for a file holding several accounts, which is split along its `account_type` column
(`split_by_account_type.py`):

- A file without the column is returned untouched.
- A file with a single account type is renamed, not rewritten: `chequing-<original name>`.
- A mixed file is split into one file per type, rows copied field for field in their
  original order. A blank type goes to `unknown-...`, and a name that's already taken gets
  `-2`, `-3`, ...

### Fallback: read the activity feed

If any step of the export dialog fails, the scraper saves a screenshot
(`wealthsimple_csv_export_unavailable`) and reads the feed on the same page instead:

1. Wait for the page shell (the Download button or `[data-fullstory="page-activity"]`), then
   for the rows. No rows after 20 s means an empty feed, not a failure.
2. **Load more**: while the oldest day header is newer than `since_date`, click
   `//button[.//span[normalize-space()="Load more"]]` and wait for the row count to grow.
3. **Read** the feed in one pass of the DOM:
   - Day headers are the `<h3>`s under `[data-fullstory="page-activity"]`: "October 14, 2026",
     "Today" or "Yesterday".
   - Rows are the accordion buttons there (`button[aria-expanded]`) that start with an icon
     span (`:scope > span > span[aria-hidden="true"]`). The filter panels' accordions don't.
   - Each row takes the date of the header above it. Rows older than `since_date` are dropped.
4. **Parse** each row from its collapsed header; rows are never expanded:
   - the title is a payee, or a ticker when the icon holds a logo with an `aria-label`;
   - the subtitle holds the type and the account, or `From: X` / `To: Y` for a transfer;
   - the last field is the amount if it looks like `$1,234.56`;
   - `[data-fs-privacy-rule="unmask"]` is the status badge. Cancelled rows are dropped.

   Rows are parsed twice: the account names collected in the first pass are what tell a
   `[type, account, account]` row (a "Cash back" between two of your own accounts) apart from
   an ordinary `[payee, type, account]` row.
5. Normalize to `Date, Payee, Account, Notes, Amount`, newest first
   (`normalize_financial_data.py`), and write one `wealthsimple_activity_<newest date>.csv`.

If the fallback fails too, the error names both failures, since only one message reaches the
web UI.

## 6. Daily earnings (optional)

Only when the request names `account_ids`, such as `tfsa-l0re4cur` (the end of the account's
`/app/account-details/...` URL). The ids are checked before anything is downloaded. The
activity export can't show this, because it lists what moved, not what the market did.

1. **Borrow the app's credentials.** Start recording the headers of every request to
   `/graphql` (CDP `Network.requestWillBeSent`, so the page's CSP and transport don't matter).
   For each account in turn until one works:
   - open `/app/account-details/<account_id>`;
   - click the chart toolbar's **Account value** tab, then **1Y**
     (`//div[@role="tablist"]//button[@role="tab" and .//span[normalize-space()="<label>"]]`).
     Neither click is required.
   - wait up to `GRAPH_HEADERS` (45 s) for the page's own `FetchAccountGraphData` request.

   If no graph request is seen, any other recorded GraphQL request's credentials are used,
   without the operation hash. No GraphQL request at all is an error (the session may have
   been signed out). Header values are never logged, only their names and counts.
2. **Query each account's one-year graph** from inside the page (so it carries the session
   cookies and the app's origin), with the borrowed headers and a `GRAPH_REQUEST` (60 s) limit
   enforced in the page.
3. **Build the series** (`account_earnings.py`). Each point has `netLiquidationValue` (what the
   account was worth) and `netDeposits` (the running total paid in). For each day:
   - `net_deposit = net_deposits(day) − net_deposits(previous day)`;
   - `earnings = (value(day) − value(previous day)) − net_deposit`.

   Each non-zero half is written as its own line, `entry_type` `deposit` or `earning`. Missing
   days are carried forward and marked `reported=false`. The series ends yesterday, because
   WS publishes a day's close after the day ends.
4. Write `wealthsimple_earnings_<account_id>_<first day>_<last day>.csv` with the columns
   `date, account_id, currency, entry_type, amount, account_value, net_deposits_total, reported`.

An account that fails is logged and skipped, and the rest are still written. If no account's
graph can be read, the activity export is served alone.

## 7. Return the files

The scraper returns the activity CSVs (split as above, or the single fallback file) followed
by any earnings CSVs. The router copies them into the retained exports folder. Over HTTP, one
file is served as itself and several as `{"files": [{"filename": ..., "content_base64": ...}]}`.
The web UI saves each one as its own download from `/exports/{job_id}/file`.

The router closes the session (and the browser) after the export, whether it worked or not.
There's no explicit sign-out.

---

## Differences from NBC

| | Wealthsimple | NBC |
|---|---|---|
| Transactions come from | WS's own CSV export (feed parsing only as a fallback) | Reading the transaction table |
| Date range | Fixed 3, 6 or 12 month windows; the whole window is returned | Last 12 months, trimmed to `since_date` |
| Files | One per account as WS writes them, split by `account_type` if combined | One per Account column value, written by PennySpy |
| Code message | Fixed text, or "tap Yes in the app" for a trusted-device approval | Read from the page (channel and masked destination) |
| Wrong code | Ends the session; log in again | Same session takes another try |
| Extras | Daily earnings per account from the value graph | — |
