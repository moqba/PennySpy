# National Bank (NBC) scraping flow

How the NBC scraper gets from the first page load to the per-account CSVs. Selectors come
from pages saved on 2026-10-17 (English UI). NBC's front end is a React app with generated
class names (`sc-htoDjs`, `cfSOAz`), so prefer `id`, `name`, `data-test` and visible text over
classes.

Domains involved:

| Domain | Used for |
|---|---|
| `connexion.bnc.ca` | Entry point, sign-in and OTP |
| `app.bnc.ca` | Online banking once signed in (`/dashboard`, `/accounts`) |

Like Wealthsimple, the flow is split in two because a person has to read the OTP: **login**
runs steps 1 to 3a and stops at the OTP prompt, and **verify + scrape** runs steps 3b to 6
once the code arrives.

---

## 1. Open the entry page

1. Navigate to `https://connexion.bnc.ca/`.
2. **Cookie banner (optional probe).** A Didomi "Respecting your privacy" dialog may cover the
   page.
   - Container: `#didomi-popup`
   - Accept: `button#didomi-notice-agree-button` (text **Accept**)

   Click Accept if it shows up within a short timeout. If it doesn't, log that it wasn't found
   and continue. It can also appear later, on `app.bnc.ca`, so run the same probe again after
   sign-in.
3. **"You can't access this page from this link".** A direct visit usually lands on an error
   page (`h1.error-display__title`) instead of the form. Click its button:
   - `button.button__redirection` (text **Back to sign-in**)

   If the sign-in form (step 2) is already showing, skip this click.

## 2. Sign in

Wait for the form, then fill it in:

| Field | Selector | Notes |
|---|---|---|
| Email ID | `input#identity` (`name="identity"`, `type="email"`) | |
| Password | `input#password` (`name="password"`, `type="password"`) | |
| Remember my email ID | `input#rememberMe-check` | Leave it unchecked |
| Sign in | `button.login__submit[type="submit"]` (text **Sign in**) | |

The page shows a **Français** toggle (`button[aria-label="Consulter le site en français"]`),
which means the English UI is active. Leave it alone: the date parsing in step 5 expects English
month names.

Credentials come from the saved login (`LoginCredentials`). Only call `.reveal()` while typing
them into the form, and never log them.

Failure checks after submit: an error message on the form (wrong password or locked account), or
a timeout waiting for step 3. In either case, raise a contextual error and take a screenshot.

## 3. One-time code (OTP)

### 3a. Read where the code went and tell the user

The OTP page sits inside `#multiFactorAuth-common`:

| Element | Selector | Example text |
|---|---|---|
| Title | `#multiFactorAuth-common h1` | "Verification code" |
| Channel sentence | `#multiFactorAuth-common h2 span` | "A verification code has been sent to you **by email** to confirm your identity." |
| Destination | `.sending-notice .dsc-alert__title span` | "Code sent to ⏎ j...e@example.com" (`<br>` between the two) |
| Code input | `input#code` (`name="code"`, `maxlength="6"`) | |
| Confirm | `#multiFactorAuth-common button[type="submit"]` (text **Confirm**) | |
| Back to sign-in | `button.back-link-button` | Don't click it |

The code doesn't always go by email: depending on the profile it can come by SMS or a phone
call. Don't hard-code the channel. Read it from the page:

1. Get the channel from the `h2` sentence ("by email", "by text message", "by phone", ...).
   Fall back to the raw sentence if no keyword matches.
2. Get the destination from the alert title, after "Code sent to". NBC already masks it
   (`j...e@…`, or the last digits of a phone number), so it's safe to show and log.
3. Return a short plain message alongside `status: "needs_otp"` and show it in the web UI, for
   example:

   > NBC sent a 6-digit code by email to j...e@example.com. Enter it to continue.

   If the page can't be parsed, fall back to: "NBC sent a verification code. Check your email
   or phone."

Login stops here and keeps the browser session open for verify.

### 3b. Submit the code

1. Type the code from the user into `input#code` (redacted in logs).
2. Click **Confirm**.
3. Wait for `app.bnc.ca/dashboard`. Treat these as failures: an error message under the input
   (`.mfa-validation-code .dsc-message--error`, e.g. a wrong or expired code), or the page still
   on the OTP form after the timeout. On a wrong code, report the error and let the user retry
   in the same session rather than starting over.
4. Run the cookie banner probe again (see step 1).

## 4. Dashboard to transaction history

1. On the dashboard, click **See all my accounts**:
   `button[data-test="button"]` whose text is "See all my accounts" (classes
   `dsc-btn is-outlined`). Match on the text, because many buttons share `data-test="button"`.
   This opens `https://app.bnc.ca/accounts`.
2. On the accounts page, open the **Transaction history** tab:
   - Tab list: `ul#accounts-tabs__tab-list`
   - Tab: `li[role="tab"]` with text "Transaction history" (currently `#react-tabs-2`, but react-tabs
     generates the ids, so match on the text)
   - Done when the tab has `aria-selected="true"` and the panel
     `.transactions-panel.react-tabs__tab-panel--selected` holds the heading
     "My transaction history".

## 5. Filter and scrape the transactions

### Filter

1. Period: `select#filter-form__period` (`name="period"`). Pick **Last 12 months**,
   `value="ONE_YEAR"`. It's a native `<select>`, so use Selenium's `Select`.

   | value | text |
   |---|---|
   | `LAST` | Last 30 days |
   | `THREE_MONTHS` | Last 3 months |
   | `SIX_MONTHS` | Last 6 months |
   | `ONE_YEAR` | Last 12 months |
   | `PERSONALIZED` | Personalize dates |

   Leave Category (`#filter-form__category` = `all`), Minimum/Maximum amount and Cheques at
   their defaults.
2. Click **Apply**: `button#filter-form__display`.
3. Wait for the table to reload, either for the row set to change or for a spinner to
   disappear.

### Table structure

The table is a grid of `div`s, not a `<table>`:

```
div.transactions-history__table[role="table"]
  div[role="rowgroup"]                  <- header row
  div[role="rowgroup"]                  <- data rows
    div.transactions-history__row[role="row"]
      .transactions-table__date         p          "October 14, 2026"
      .transactions-table__description             "INTERAC e-Transfer"
      .transactions-table__account                 "Chequing Account"
      .transactions-table__category     p          "Transfer"
      .transactions-table__amount       p.is-credit|p.is-debit > span.dsc-currencytext  "$400.00"
```

Parsing each row:

| Field | How |
|---|---|
| date | Parse the text with `%B %d, %Y` → ISO `YYYY-MM-DD` |
| description | Cell text, trimmed |
| account | Cell text, trimmed. This is the account's display name and decides the output file |
| category | Text of the inner `p` |
| amount | Strip `$` and `,` → decimal. Make it negative when the `p` has `is-debit` and positive when it has `is-credit` |

The rows are sorted by date, newest first (the Date header has `aria-sort="descending"`).
Check that before relying on the order.

### Stop at the user's date

The web UI sends a `since_date`. Read the rows top to bottom:

- keep a row when its date is on or after `since_date`;
- stop as soon as a row is older than `since_date`, since everything below it is older too.

If `since_date` is more than 12 months back, "Last 12 months" can't reach it. Scrape what is
there and add a warning to the result saying the export starts on the oldest date shown.
(`PERSONALIZED` dates could reach further back. That's a possible later improvement.)

### Paging (to check on the live site)

The saved page showed 6 rows with no "show more" control in the transactions panel, and the
app ships a `pagination` module. Before the table counts as fully read, check on the live
site for any of these:

- a "Show more / Load more" button under the table, which you keep clicking until it's gone or
  the last row is older than `since_date`;
- pagination controls, which you step through;
- infinite scroll, where you scroll the container and wait for the row count to stop growing.

Whichever it turns out to be, cap the number of iterations and log the row count each round.

## 6. Split into one CSV per account

Group the kept rows by the **Account** cell text and write one CSV per account:

- File name: `<account_slug>_<date>.csv`
  - `account_slug`: the account name lower-cased, with each run of non-alphanumeric characters
    replaced by `_` and leading/trailing `_` trimmed. For example, `Chequing Account` →
    `chequing_account` and `High Interest Savings Account` → `high_interest_savings_account`.
  - `date`: the date of the export (`YYYY-MM-DD`).
- If two accounts slug to the same name, add a numeric suffix (`_2`) and log a warning.
- Columns: `date,description,category,amount`, plus `account` so a file still makes sense on
  its own, and `status` (`posted` or `pending`). Rows stay newest first.
- A pending row has no date yet: its date cell holds `.transactions-table__pending` ("Pending",
  or "En attente" in French). It is kept, dated the day of the export, with status `pending`.
- Return the files the way the WS multi-file export does:
  `{"files": [{"filename": ..., "content_base64": ...}]}`.

Example:

```
chequing_account_2026-10-17.csv
high_interest_savings_account_2026-10-17.csv
```

## 7. Sign out

Click **Sign out** (`button[aria-label="Sign out"]`, the desktop variant
`.ssoHeader__logOutDesktop`), then close the browser. Do this when the scrape fails too.

---

## Open questions

- **OTP wording for SMS/phone.** Only the email variant has been seen so far. Record the
  `h2` text when an SMS code is used and adjust the channel matching.
- **Paging** on the transaction table (see step 5).
- **Account cell for cards.** The saved page only showed bank accounts. Check what the Account
  cell says for a credit card. If two products have the same display name, the account number
  suffix (if one is shown anywhere) is needed to tell them apart.
- **Pending transactions.** Check whether they show in this table and how they are marked.
