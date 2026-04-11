# Usage
> [!important]
The scraper is only compatible with users using mobile 2FA. If your RBC account is set up with SMS 2FA,
there is no current integration for this method.
After calling `/rbc/login`, call `/rbc/verify` — it will block for up to 5 minutes while waiting for you
to approve the sign-in request in your RBC mobile app.
Message-based 2FA is a potential future enhancement, though not a priority at this time.
>

Regardless of the installation method, the following env variables are required:
```dotenv
PENNYSPY_RBCU="rbc_username"
PENNYSPY_RBCP="rbc_password"
```
It is recommended to make an `.env` file containing these.

## RBC API

1. **`POST /rbc/login`** — Launches a browser session, submits credentials, and waits for the 2FA prompt in the RBC app to appear. Returns a `session_id`.
2. **`POST /rbc/verify`** — Blocks until you approve the sign-in request in your RBC mobile app (up to 5 minutes). Returns once authenticated.
3. **`POST /rbc/scrape`** — Downloads transactions and returns a file.

---

#### `POST /rbc/login`

Initiate an RBC login. Credentials are read from environment variables.

**Request body:** none

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "waiting_for_external"
}
```

---

#### `POST /rbc/verify`

Wait for mobile 2FA approval. This call blocks until you approve the sign-in in your RBC app.

**Request body (JSON):**

| Name       | Type   | Required | Description                               |
|------------|--------|----------|-------------------------------------------|
| session_id | string | yes      | Session ID returned by `/rbc/login`       |

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "authenticated"
}
```

---

#### `POST /rbc/scrape`

Download transactions as a file.

**Request body (JSON):**

| Name         | Type   | Required | Description                                                                         |
|--------------|--------|----------|-------------------------------------------------------------------------------------|
| session_id   | string | yes      | Session ID returned by `/rbc/login`                                                 |
| software     | string | yes      | Target software format for export — see [Software](#software)                       |
| account_info | string | yes      | Account(s) to include — see [AccountInfo](#accountinfo)                             |
| include      | string | yes      | Which operations to include — see [Include](#include)                               |

**Response:** A downloadable file in the format matching the selected `software`.

**Error responses:**

| Status | Cause                                                      |
|--------|------------------------------------------------------------|
| 400    | Invalid credentials or missing required parameter          |
| 404    | `session_id` not found (call login first)                  |
| 408    | 2FA approval timed out (5 minutes with no mobile response) |
| 500    | Unexpected scraping error                                  |

---

## Options

### Software

| Name                  | Value               | Extension |
|-----------------------|---------------------|-----------|
| QUICKEN               | `"QUICKEN"`         | .ofx      |
| MAKISOFT              | `"MAKISOFT"`        | .afx      |
| QUICKBOOKS            | `"QUICKBOOKS"`      | .qbo      |
| MONEY                 | `"MONEY"`           | .ofx      |
| MAKISOFT_COMPTABILITY | `"MAKISOFTB"`       | .afx      |
| SIMPLY_ACCOUNTING     | `"SIMPLYACCOUNTING"`| .aso      |
| CSV                   | `"EXCEL"`           | .csv      |

### AccountInfo

| Name               | Value    |
|--------------------|----------|
| ALL_ACCOUNTS       | `"A"`    |
| CHECKING_ACCOUNTS  | `"B"`    |
| CREDIT_ACCOUNTS    | `"VALL"` |
| PRIMARY_CHECKING   | `"C001"` |
| SECONDARY_CHECKING | `"C002"` |

### Include

| Name           | Value |
|----------------|-------|
| NEW_OPERATIONS | `"N"` |
| ALL_OPERATIONS | `"A"` |
