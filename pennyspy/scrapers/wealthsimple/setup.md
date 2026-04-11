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
3. **`POST /ws/scrape`** — Scrapes activity since the given date and returns normalized data as a CSV file.

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

Retrieve normalized transaction activity as a CSV.

**Request body (JSON):**

| Name       | Type   | Required | Description                                              |
|------------|--------|----------|----------------------------------------------------------|
| session_id | string | yes      | Session ID returned by `/ws/login`                       |
| since_date | string | yes      | Fetch activity on or after this date (`YYYY-MM-DD`)      |

**Response:** A downloadable CSV file named `wealthsimple_activity.csv`.

**CSV columns:**

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

ws.download_transactions(
    export_directory=Path("."),
    since_date=date(2025, 1, 1),
)
```
