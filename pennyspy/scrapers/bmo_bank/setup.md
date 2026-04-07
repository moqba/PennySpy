# Usage
> [!important]
The scraper uses OTP-based phone 2FA. After calling `/bmo/login`, check your phone for a code and pass it to `/bmo/scrape` within the 10-minute session timeout.
>

Regardless of the installation method, the following env variables are required:
```dotenv
PENNYSPY_BMOU="bmo_username"
PENNYSPY_BMOPP="bmo_password"
```
It is recommended to make an `.env` file containing these.

## BMO API

Because BMO requires a manual OTP, the flow is split across two endpoints:

1. **`POST /bmo/login`** — Launches a browser session, submits credentials, and triggers the OTP to be sent to your phone. Returns a `session_id` to use in the next step.
2. **`POST /bmo/scrape`** — Submits the OTP, downloads or scrapes transactions, and returns a file.

---

#### `POST /bmo/login`

Initiate a BMO login. Credentials are read from environment variables.

**Request body (JSON):**

| Name         | Type   | Required | Description                                              |
|--------------|--------|----------|----------------------------------------------------------|
| account_uuid | string | yes      | UUID of the BMO credit card account to target            |

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

#### `POST /bmo/scrape`

Complete the 2FA and retrieve transactions as a downloadable file.

**Request body (JSON):**

| Name           | Type   | Required            | Description                                                                 |
|----------------|--------|---------------------|-----------------------------------------------------------------------------|
| session_id     | string | yes                 | Session ID returned by `/bmo/login`                                         |
| otp_code       | string | yes                 | OTP code sent to your phone                                                 |
| app_type       | string | yes                 | Export format — see [AppType](#apptype)                                     |
| statement_date | string | if not `csv_web`    | Statement period to export — see [StatementDate](#statementdate)            |
| until_date     | string | if `csv_web`        | Fetch transactions on or after this date (`YYYY-MM-DD`), web-parsed         |

**Response:** A downloadable file whose name and format depend on the selected `app_type`.

**Error responses:**

| Status | Cause                                                         |
|--------|---------------------------------------------------------------|
| 400    | Invalid credentials, bad OTP, or missing required parameter   |
| 404    | `session_id` not found (call login first)                     |
| 500    | Unexpected scraping error                                     |

---

## Options

### AppType

| Name       | Value        | Extension | Notes                                      |
|------------|--------------|-----------|--------------------------------------------|
| CSV        | `"csv"`      | .csv      | Downloaded via API                         |
| CSV_WEB    | `"csv_web"`  | .csv      | Parsed from the web UI; requires `until_date` |
| MSMONEY    | `"msmoney"`  | .ofx      |                                            |
| QUICKEN    | `"quicken"`  | .qfx      |                                            |
| QUICKBOOKS | `"quickbooks"`| .qbo     |                                            |
| SIMPLY_ACC | `"simplyacc"`| .aso      |                                            |

### StatementDate

| Name   | Value      | Description                  |
|--------|------------|------------------------------|
| RECENT | `"recent"` | Current statement period     |
| ALL    | `"all"`    | All available transactions   |

---

# Python call

```python
from pennyspy.scrapers.bmo_bank.bmo_bank import BMOBank
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate

bank = BMOBank()

# Replace with your credit card account UUID (visible in the BMO URL when viewing account details)
bank.initiate_login(account_uuid="your-account-uuid-here")

otp = input("Enter OTP code sent to your phone: ")
bank.complete_2fa(otp)

# Download via API (requires statement_date)
file_path = bank.download_transactions(
    app_type=AppType.QUICKEN,
    statement_date=StatementDate.ALL,
)

# Or parse directly from the web UI (requires until_date)
from datetime import datetime
file_path = bank.parse_transactions_from_web(
    until_date=datetime(2025, 1, 1),
)
```
