# Usage
> [!important]
The scraper uses OTP-based phone 2FA. After calling `/bmo/login`, check your phone for a code and pass it
to `/bmo/verify` within the 10-minute session timeout. Once verified, call `/bmo/scrape` to download transactions.
>

> [!important]
The scraper now only supports credit cards.
>

Regardless of the installation method, the following env variables are required:
```dotenv
PENNYSPY_BMOU="bmo_username"
PENNYSPY_BMOPP="bmo_password"
```
It is recommended to make an `.env` file containing these.

For the account_uid, refer to the [login section of the api](#uuid_guide).

The API exposes BMO’s native download request interface. However, the transaction export format differs between the official web/mobile UI and the raw BMO download endpoints. To address this, a CSV_WEB option has been introduced, which parses data directly from the UI layer and produces a CSV that closely matches what users see in their online banking interface.
## BMO API

Because BMO requires a manual OTP, the flow is split across three endpoints:

1. **`POST /bmo/login`** — Launches a browser session, submits credentials, and triggers the OTP to be sent to your phone. Returns a `session_id` to use in the next step.
2. **`POST /bmo/verify`** — Submits the OTP code to complete 2FA authentication.
3. **`POST /bmo/scrape`** — Downloads or scrapes transactions and returns a file.

---
#### `POST /bmo/login`

Initiate a BMO login. Credentials are read from environment variables.

<a id="**uuid_guide**"></a>
The account_uuid can be found on the BMO website. When selecting the specific account of interest. the URL would contain the account_uuid as such : `https://www1.bmo.com/banking/digital/account-details/cc/<account_uuid>`

**Request body (JSON):**

| Name         | Type   | Required | Description                                              |
|--------------|--------|----------|----------------------------------------------------------|
| account_uuid | string | yes      | UUID of the BMO credit card account to target            |

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "needs_otp"
}
```

---

#### `POST /bmo/verify`

Submit the OTP code to complete 2FA.

**Request body (JSON):**

| Name       | Type   | Required | Description                               |
|------------|--------|----------|-------------------------------------------|
| session_id | string | yes      | Session ID returned by `/bmo/login`       |
| otp_code   | string | yes      | OTP code sent to your phone               |

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "authenticated"
}
```

---

#### `POST /bmo/scrape`

Retrieve transactions as a downloadable file.

**Request body (JSON):**

| Name           | Type   | Required         | Description                                                                 |
|----------------|--------|------------------|-----------------------------------------------------------------------------|
| session_id     | string | yes              | Session ID returned by `/bmo/login`                                         |
| app_type       | string | yes              | Export format — see [AppType](#apptype)                                     |
| statement_date | string | if not `csv_web` | Statement period to export — see [StatementDate](#statementdate)            |
| from_date      | string | if `csv_web`     | Fetch transactions on or after this date (`YYYY-MM-DD`), web-parsed         |

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

| Name       | Value         | Extension | Notes                                                             |
|------------|---------------|-----------|-------------------------------------------------------------------|
| CSV        | `"csv"`       | .csv      | Downloaded via API                                                |
| CSV_WEB    | `"csv_web"`   | .csv      | (recommended option) Parsed from the web UI; requires `from_date` |
| MSMONEY    | `"msmoney"`   | .ofx      |                                                                   |
| QUICKEN    | `"quicken"`   | .qfx      |                                                                   |
| QUICKBOOKS | `"quickbooks"`| .qbo      |                                                                   |
| SIMPLY_ACC | `"simplyacc"` | .aso      |                                                                   |

### StatementDate

| Name   | Value      | Description                  |
|--------|------------|------------------------------|
| RECENT | `"recent"` | Current statement period     |
| ALL    | `"all"`    | All available transactions   |

---

# Python call

```python
from pathlib import Path
from pennyspy.scrapers.bmo_bank.bmo_bank import BMOBank
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate

bank = BMOBank()

# Replace with your credit card account UUID (visible in the BMO URL when viewing account details)
bank.start_auth(account_uuid="your-account-uuid-here")

otp = input("Enter OTP code sent to your phone: ")
bank.continue_auth(otp_code=otp)

# Download via API (requires statement_date)
bank.download_transactions(
    export_directory=Path("."),
    app_type=AppType.QUICKEN,
    statement_date=StatementDate.ALL,
)

# Or parse directly from the web UI (requires from_date)
from datetime import datetime
bank.download_transactions(
    export_directory=Path("."),
    app_type=AppType.CSV_WEB,
    from_date=datetime(2025, 1, 1),
)
```
