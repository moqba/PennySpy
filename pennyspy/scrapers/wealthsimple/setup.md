# Usage
> [!important]
The scraper uses OTP-based mobile 2FA. After calling `/ws/login`, check your Wealthsimple mobile app or
SMS for a 6-digit code and pass it to `/ws/verify` within the session timeout window.
>

Regardless of the installation method, the following env variables are required:
```dotenv
PENNYSPY_WSU="wealthsimple_email"
PENNYSPY_WSP="wealthsimple_password"
```
It is recommended to make an `.env` file containing these.

## Wealthsimple API

Because Wealthsimple requires a manual OTP, the flow is split across endpoints:

1. **`POST /ws/login`** — Launches a browser session, submits credentials, and waits for the OTP prompt. Returns a `session_id` to use in the next step.
2. **`POST /ws/verify`** — Submits the OTP code to complete 2FA authentication.
3. **`POST /ws/scrape`** — Downloads Wealthsimple's own activity export and returns it as-is, plus a day-by-day deposit and earnings series for any accounts named in `account_ids`.

One session serves one download, so everything you want comes out of the same `/ws/scrape`
call — naming accounts adds their earnings CSVs to it rather than costing a second login.

Activity comes from the **Download activities** export on `my.wealthsimple.com/app/activity`.
The scraper picks the shortest period the dialog offers (3, 6 or 12 months) that still covers
`since_date`, selects **All accounts**, and returns the CSVs the browser downloads without
modifying them. WS can serve one file per account; several files are bundled into a ZIP with
each entry unchanged. If the export dialog is unavailable, the scraper falls back to parsing
the activity feed directly, which produces the normalized CSV described below instead.

---

#### `POST /ws/login`

Initiate a Wealthsimple login. Credentials are read from environment variables.

**Request body:** none

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "needs_otp"
}
```

---

#### `POST /ws/verify`

Submit the OTP code to complete 2FA.

**Request body (JSON):**

| Name       | Type   | Required | Description                                  |
|------------|--------|----------|----------------------------------------------|
| session_id | string | yes      | Session ID returned by `/ws/login`           |
| otp_code   | string | yes      | 6-digit OTP from Wealthsimple mobile app     |

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "authenticated"
}
```

---

#### `POST /ws/scrape`

Retrieve transaction activity, and optionally a daily earnings series beside it.

**Request body (JSON):**

| Name        | Type     | Required | Description                                                                 |
|-------------|----------|----------|------------------------------------------------------------------------------|
| session_id  | string   | yes      | Session ID returned by `/ws/login`                                          |
| since_date  | string   | yes      | Selects the export window (`YYYY-MM-DD`); see the note below                 |
| account_ids | string[] | no       | Accounts to also produce a [daily earnings](#daily-earnings) CSV for; one CSV each |

Wealthsimple only exports whole windows of 3, 6 or 12 months, so `since_date` selects the
shortest window that covers it and the response contains that entire window — including rows
older than `since_date`. Dates further back than 12 months are capped at 12 months. The
earnings series uses `since_date` as its own first day, exactly as asked for.

**Response:** Wealthsimple exports one CSV per account, and every one of them is served,
followed by one earnings CSV per requested account.

- **One export** — the file itself (`application/octet-stream`), e.g. `activities-export-2026-08-02.csv`.
- **Several exports** — a JSON envelope holding each file, never an archive:

  ```json
  {
    "files": [
      { "filename": "activities-export-TFSA.csv", "content_base64": "dHJhbnNhY3Rpb25f…" },
      { "filename": "activities-export-Cash.csv", "content_base64": "dHJhbnNhY3Rpb25f…" }
    ]
  }
  ```

  The web UI saves each one as its own download. To do the same from the shell:

  ```bash
  curl -s -X POST localhost:8000/ws/scrape -H 'Content-Type: application/json' -d @body.json \
    | jq -r '.files[] | .filename + " " + .content_base64' \
    | while read -r name content; do echo "$content" | base64 -d > "$name"; done
  ```

Either way the CSVs are byte-for-byte what Wealthsimple wrote.

**Export columns** (Wealthsimple's own, passed through unchanged):

`transaction_date`, `settlement_date`, `account_id`, `account_type`, `activity_type`,
`activity_sub_type`, `description`, `direction`, `symbol`, `name`, `currency`, `quantity`,
`unit_price`, `commission`, `net_cash_amount`

**Fallback response:** if the export dialog is unavailable the activity feed is parsed instead,
producing `wealthsimple_activity_<date>.csv` with these columns:

| Column  | Description                                                      |
|---------|------------------------------------------------------------------|
| Date    | Transaction date (ISO 8601)                                      |
| Payee   | Counterparty (ticker symbol, person, or institution)             |
| Account | Source account (e.g., Chequing • Main, TFSA)                     |
| Notes   | Transaction details (e.g., "Limit buy 10 @ $50.00")             |
| Amount  | Signed amount in CAD (negative = expense, positive = income)     |

---

#### Daily earnings

Naming `account_ids` on `/ws/scrape` adds a day-by-day deposit and earnings series per
account to the response. It answers what the activity export cannot: the export lists what
moved, not what the market did, so it cannot say what an account *earned*.

An account ID is the last path segment of its page URL —
`my.wealthsimple.com/app/account-details/tfsa-l0re4cur` → `tfsa-l0re4cur`. It is validated
before anything is downloaded, so a mistyped one comes back as a 400 rather than after
several minutes of scraping.

**How each day is derived.** The account-details page draws its "Account value" chart from one
GraphQL query, and every point on it carries both `netLiquidationValue` (what the account was
worth) and `netDeposits` (the running total of money moved in and out). Each day's change is
therefore split in two:

```
value_change = account_value(day) − account_value(previous day)
net_deposit  = net_deposits(day)  − net_deposits(previous day)
earnings     = value_change − net_deposit
```

`earnings` is what the account did on its own — market moves, dividends, interest, fees — with
deposits and withdrawals taken back out. Notes:

- **Each half is its own line.** A day where money went in *and* the market moved produces two
  lines sharing one date — one `deposit`, one `earning` — told apart by the `entry_type` column.
- **A half that came to zero writes no line.** Most days carry no deposit, and a `0.00` deposit
  line on every one of them would be noise; a day where nothing at all moved contributes no
  lines. A day's lines therefore add up to that day's whole change in account value.
- The **first day of the series produces no row**: it has no previous day to subtract.
  The day before `since_date` is fetched and used for exactly this, but is not returned.
- **Every calendar day is accounted for.** Wealthsimple already plots one point per calendar
  date, weekends and holidays included — a non-trading day repeats the previous close and so
  comes to nothing. Any gap that does appear is closed the same way, and `reported` marks the
  lines whose day was carried forward here rather than sent by WS.
- **The series ends yesterday.** A day's close is published once the day is over, so the
  graph runs to the previous day. A window ending today therefore ends at yesterday rather
  than repeating yesterday's figure as if today had been valued.
- **The graph is always fetched over a year** and the rows are trimmed to `since_date`
  afterwards, whichever export window was chosen. Every window the UI offers fits inside a
  year, so a shorter range would only save response size. An earlier `since_date` is honoured
  as far as the data goes — one year — and the shortfall is logged.

**Response:** one earnings CSV per account, alongside the activity export and served the same
way — the file itself when the scrape produced only one, a JSON envelope of base64 contents
otherwise. Files are named `wealthsimple_earnings_<account_id>_<first day>_<last day>.csv`.

**Columns:**

| Column               | Description                                                          |
|----------------------|----------------------------------------------------------------------|
| `date`               | The day the line falls on (`YYYY-MM-DD`)                             |
| `account_id`         | The account the line belongs to                                      |
| `currency`           | Currency of every amount in the line                                 |
| `entry_type`         | `deposit` or `earning` — which half of the day's change this line is  |
| `amount`             | That half; negative for a withdrawal or a losing day                 |
| `account_value`      | What the account was worth at the end of the day                     |
| `net_deposits_total` | Running total of money moved in and out since the account opened     |
| `reported`           | `true` when WS plotted that day, `false` when it was carried forward |

`account_value`, `net_deposits_total` and `reported` describe the day, so they repeat across a
day's two lines. There is deliberately no day-total column: a total repeated on both lines would
double as soon as the column was summed, and it is anyway the sum of the day's own `amount`s.

```csv
date,account_id,currency,entry_type,amount,account_value,net_deposits_total,reported
2026-08-04,tfsa-l0re4cur,CAD,deposit,500.00,1510.00,1500.00,true
2026-08-04,tfsa-l0re4cur,CAD,earning,10.00,1510.00,1500.00,true
2026-08-05,tfsa-l0re4cur,CAD,earning,12.35,1522.35,1500.00,true
```

An account that cannot be read does not fail the request while others can still be served; it
is logged and left out of the response. A graph that answers for no account at all is logged
too and costs only the earnings files — the activity export has been downloaded by then, and
it is what most of the request was for.

**Error responses:**

| Status | Cause                                     |
|--------|-------------------------------------------|
| 400    | Invalid credentials, bad OTP code, or a malformed account ID |
| 404    | `session_id` not found (call login first) |
| 500    | Unexpected scraping error                 |

---

# Python call

```python
from pathlib import Path
from datetime import date
from pennyspy.scrapers.wealthsimple.wealthsimple import Wealthsimple

ws = Wealthsimple()
ws.start_auth()  # submits credentials, triggers OTP

otp = input("Enter 2FA code: ")
ws.continue_auth(otp_code=otp)

paths = ws.download_transaction_files(
    export_directory=Path("."),
    since_date=date(2025, 1, 1),
    account_ids=["tfsa-l0re4cur", "rrsp-9kzq1w2e"],  # optional; adds one earnings CSV each
)
```

`download_transaction_files` returns every export — one path per account, then one earnings
CSV per requested account. `download_transactions` is the single-path variant of the same
call: it returns the file when there is only one and otherwise bundles them into a ZIP, purely
to satisfy that one-path contract. Nothing over HTTP takes that route.

To build the earnings series on its own, without the activity export, call
`download_account_earnings` on the same authenticated scraper. It is the only way to ask for a
window that ends before today:

```python
paths = ws.download_account_earnings(
    export_directory=Path("."),
    account_ids=["tfsa-l0re4cur", "rrsp-9kzq1w2e"],
    since_date=date(2025, 8, 1),
    until_date=date(2026, 8, 1),  # optional, defaults to today
)
```
