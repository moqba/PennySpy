# Usage
> [!important]
NBC asks for a 6-digit verification code at every sign-in from a new browser. After
`/nbc/login`, the response's `message` says where NBC sent it (email, text message or phone
call, and the masked address). Pass the code to `/nbc/verify` within the session timeout window.
>

## Logins
Add your NBC login (email ID and password) on the **Logins** page of the web UI
(`/app/credentials.html`). It is stored encrypted on the server and never shown again; you can
save several and pick one on the NBC page. See [Bank logins](../../../README.md#bank-logins).
NBC has no env-variable fallback.

## NBC API

The flow is split across endpoints because a person has to read the code:

1. **`POST /nbc/login`**: opens a browser, signs in and waits on the code page. Returns a
   `session_id` and a `message` saying where the code went.
2. **`POST /nbc/verify`**: submits the code. If NBC rejects it, the answer is `needs_otp`
   again with NBC's reason in `message`, and the same session takes another try.
3. **`POST /nbc/scrape`**: reads the Transaction history tab from `since_date` to today and
   returns one CSV per account.

Each step can also run as a background job: `POST /nbc/login/jobs`, `POST /nbc/verify/jobs`
and `POST /nbc/export` take the same bodies and return a `job_id` to follow on
`/jobs/{job_id}`. A finished export's files download from `/exports/{job_id}/file`. The web UI
works this way. See [Jobs](../../../README.md#jobs).

The history tab is set to **Last 12 months** and read until a row is older than `since_date`.
A `since_date` further back than 12 months is accepted, but the files then start at the oldest
row NBC shows. How the pages are driven is described in [scraping_flow.md](scraping_flow.md).

---

#### `POST /nbc/login`

**Request body (JSON, optional):**

| Name          | Type   | Required | Description |
|---------------|--------|----------|-------------|
| credential_id | string | no       | Id of the saved login to use (from `GET /credentials?bank=nbc`). Leave it out when only one login is saved |

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "needs_otp",
  "message": "NBC sent a 6-digit code by email to j...e@example.com. Enter it to continue."
}
```

`status` is `authenticated` when NBC signed in without a code.

---

#### `POST /nbc/verify`

**Request body (JSON):**

| Name       | Type   | Required | Description                          |
|------------|--------|----------|--------------------------------------|
| session_id | string | yes      | Session ID returned by `/nbc/login`  |
| otp_code   | string | yes      | The 6-digit code NBC sent            |

**Response:** `{"session_id": ..., "status": "authenticated", "message": ""}`, or
`"status": "needs_otp"` with NBC's reason when the code was not accepted.

---

#### `POST /nbc/scrape`

**Request body (JSON):**

| Name       | Type   | Required | Description                                       |
|------------|--------|----------|---------------------------------------------------|
| session_id | string | yes      | Session ID, after `/nbc/verify` returned `authenticated` |
| since_date | date   | yes      | First day to include, `YYYY-MM-DD`                |

**Response:** one file is served as itself. Several come back as a JSON envelope,
`{"files": [{"filename": ..., "content_base64": ...}]}`.

Each account gets `<account_slug>_<export date>.csv`, named after the Account column, for
example `chequing_account_2026-10-17.csv` and `high_interest_savings_account_2026-10-17.csv`.
Columns:

| Column      | Example              | Notes |
|-------------|----------------------|-------|
| date        | `2026-10-14`         | ISO date |
| description | `INTERAC e-Transfer` | |
| category    | `Transfer`           | NBC's category |
| amount      | `-12.50`             | Negative for money out, positive for money in |
| account     | `Chequing Account`   | As NBC names it |

Rows stay newest first, as NBC lists them.
