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

Because Wealthsimple requires a manual OTP, the flow is split across three endpoints:

1. **`POST /ws/login`** — Launches a browser session, submits credentials, and waits for the OTP prompt. Returns a `session_id` to use in the next step.
2. **`POST /ws/verify`** — Submits the OTP code to complete 2FA authentication.
3. **`POST /ws/scrape`** — Downloads Wealthsimple's own activity export and returns it as-is.

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

Retrieve transaction activity.

**Request body (JSON):**

| Name       | Type   | Required | Description                                                          |
|------------|--------|----------|----------------------------------------------------------------------|
| session_id | string | yes      | Session ID returned by `/ws/login`                                   |
| since_date | string | yes      | Selects the export window (`YYYY-MM-DD`); see the note below         |

Wealthsimple only exports whole windows of 3, 6 or 12 months, so `since_date` selects the
shortest window that covers it and the response contains that entire window — including rows
older than `since_date`. Dates further back than 12 months are capped at 12 months.

**Response:** Wealthsimple exports one CSV per account, and every one of them is served.

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

**Error responses:**

| Status | Cause                                     |
|--------|-------------------------------------------|
| 400    | Invalid credentials or bad OTP code       |
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
)
```

`download_transaction_files` returns every export — one path per account. `download_transactions`
is the single-path variant of the same call: it returns the file when there is only one and
otherwise bundles them into a ZIP, purely to satisfy that one-path contract. Nothing over HTTP
takes that route.
