# Usage
> [!important]
The scraper uses 2-Step Verification (2SV) via a mobile push notification. After calling `/scotia/login`,
check your Scotiabank app and approve the sign-in request within 2 minutes. No OTP code is required —
the `/login` call blocks until the push notification is approved (or times out).
>

Regardless of the installation method, the following env variables are required:
```dotenv
PENNYSPY_SCOTIAU="scotiabank_email"
PENNYSPY_SCOTIAP="scotiabank_password"
```
It is recommended to make an `.env` file containing these.

## Scotiabank API

Because Scotiabank uses a mobile push notification for 2SV, the login call blocks until the user approves it. No separate verify step is needed:

1. **`POST /scotia/login`** — Launches a browser session, submits credentials, and waits for the 2SV push notification to be approved on your phone (up to 2 minutes). Returns a `session_id` once authenticated.
2. **`POST /scotia/scrape`** — Downloads transactions for all accounts in the given date range and returns a file.

---

#### `POST /scotia/login`

Initiate a Scotiabank login. Credentials are read from environment variables. The call blocks until 2SV is approved on your phone.

**Request body:** none

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "authenticated"
}
```

---

#### `POST /scotia/scrape`

Download transactions for all linked accounts in the specified date range.

**Request body (JSON):**

| Name         | Type   | Required | Description                                             |
|--------------|--------|----------|---------------------------------------------------------|
| session_id   | string | yes      | Session ID returned by `/scotia/login`                  |
| from_date    | string | yes      | Start of date range (`YYYY-MM-DD`)                      |
| to_date      | string | yes      | End of date range (`YYYY-MM-DD`)                        |

**Response:** A downloadable CSV file for a single account, or a ZIP archive if multiple accounts are found.

**CSV columns:**

| Column               | Description                                      |
|----------------------|--------------------------------------------------|
| Date                 | Transaction date                                 |
| PostedDate           | Date the transaction was posted                  |
| TransactionType      | Type of transaction                              |
| Description          | Transaction description                          |
| Details              | Additional details / sub-description             |
| Category             | Transaction category                             |
| MerchantName         | Merchant name (if available)                     |
| MerchantCity         | Merchant city (if available)                     |
| MerchantState        | Merchant state/province (if available)           |
| MerchantCountry      | Merchant country code (if available)             |
| MerchantCategoryCode | Merchant category code (if available)            |
| Amount               | Transaction amount (negative = debit)            |
| Currency             | Currency code (e.g. `CAD`)                       |
| Balance              | Running balance after transaction (if available) |
| Status               | Transaction status (e.g. `pending`, `settled`)   |
| PurchaseType         | Purchase type (if available)                     |
| TransactionId        | Unique transaction identifier                    |

**Error responses:**

| Status | Cause                                                       |
|--------|-------------------------------------------------------------|
| 400    | Invalid credentials, 2SV timed out, or no transactions found |
| 404    | `session_id` not found (call login first)                   |
| 500    | Unexpected scraping error                                   |

---

# Python call

```python
from pathlib import Path
from datetime import date
from pennyspy.scrapers.scotiabank.scotiabank import ScotiaBank

bank = ScotiaBank()

# Submits credentials and blocks until 2SV push notification is approved on your phone
bank.start_auth()

# Download all accounts for the given date range
bank.download_transactions(
    export_directory=Path("."),
    from_date=date(2025, 1, 1),
    to_date=date.today(),
)
```
