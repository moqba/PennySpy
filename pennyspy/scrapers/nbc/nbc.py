"""National Bank (NBC) scraper: sign in, pass the one-time code, read the transaction history.

The steps, the pages and their selectors are described in ``scraping_flow.md`` beside this
file. NBC offers no transaction export on the history tab (only "Print"), so the table is read
row by row and split into one CSV per account (see :mod:`pennyspy.scrapers.nbc.transactions`).
"""

from __future__ import annotations

import json
import logging
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from pennyspy.credentials.resolve import credentials_or_env
from pennyspy.scrapers.base import AuthStep, ZenBankScraper
from pennyspy.scrapers.get_required_env_var import SecretString
from pennyspy.scrapers.nbc.connection_element_id import (
    LAST_12_MONTHS,
    AccountsElementXpath,
    ConnectionElementCss,
    ConnectionElementXpath,
    ConsentCss,
    TransactionsCss,
)
from pennyspy.scrapers.nbc.delay_seconds import DelaySeconds
from pennyspy.scrapers.nbc.otp_notice import FALLBACK as OTP_FALLBACK
from pennyspy.scrapers.nbc.otp_notice import describe_otp
from pennyspy.scrapers.nbc.transactions import (
    TWELVE_MONTHS,
    TransactionParseError,
    coverage_note,
    keep_since,
    parse_date,
    parse_rows,
    write_account_csvs,
)
from pennyspy.scrapers.scraper import BrowserConfig
from pennyspy.scrapers.zen_scraper import (
    By,
    ScraperError,
    TimeoutException,
    clickable,
    present,
    url_contains,
    visible,
)

NBC_ENTRY: Final[str] = "https://connexion.bnc.ca/"
NBC_APP: Final[str] = "https://app.bnc.ca/"
NBC_ACCOUNTS: Final[str] = f"{NBC_APP}accounts"

# What the sign-in form shows when it turns the login down, most specific first.
_LOGIN_ERROR_LOCATORS: Final[tuple[tuple[str, str], ...]] = (
    (By.XPATH, ConnectionElementXpath.LOGIN_ERROR_IN_FORM),
    (By.XPATH, ConnectionElementXpath.LOGIN_ERROR_ALERT),
)

# The access-error page's "Back to sign-in" button: its class first, then its label.
_BACK_TO_SIGN_IN_LOCATORS: Final[tuple[tuple[str, str], ...]] = (
    (By.CSS_SELECTOR, ConnectionElementCss.BACK_TO_SIGN_IN),
    (By.XPATH, ConnectionElementXpath.BACK_TO_SIGN_IN_BY_LABEL),
)

# Reads every row of the transaction table as plain strings. The direction of an amount is
# only in the class of its <p> (is-credit / is-debit), so the classes are read too, and so is
# the marker of a pending row, whose date cell holds "Pending" instead of a date. Returned
# as JSON text: CDP hands back a string intact, whereas nested objects need extra plumbing.
_ROWS_SCRIPT: Final[str] = f"""
const text = (row, selector) => {{
  const el = row.querySelector(selector);
  return el ? el.textContent.trim() : '';
}};
const rows = [...document.querySelectorAll({json.dumps(str(TransactionsCss.ROW))})];
return JSON.stringify(rows.map((row) => {{
  const amount = row.querySelector('.transactions-table__amount p') ||
                 row.querySelector('.transactions-table__amount');
  return {{
    date: text(row, '.transactions-table__date'),
    description: text(row, '.transactions-table__description'),
    account: text(row, '.transactions-table__account'),
    category: text(row, '.transactions-table__category'),
    amount: amount ? amount.textContent.trim() : '',
    credit: !!amount && amount.classList.contains('is-credit'),
    debit: !!amount && amount.classList.contains('is-debit'),
    pending: !!row.querySelector('.transactions-table__pending'),
  }};
}}));
"""

_ROW_COUNT_SCRIPT: Final[str] = f"return document.querySelectorAll({json.dumps(str(TransactionsCss.ROW))}).length;"
_SCROLL_TO_BOTTOM_SCRIPT: Final[str] = "window.scrollTo(0, document.body.scrollHeight); return true;"

# Browser downloads are not used: the files are written from the table.
_BUNDLE_NAME: Final[str] = "nbc_transactions_{date}.zip"

logger = logging.getLogger(__name__)


def is_signed_in_url(url: str) -> bool:
    """Whether ``url`` is a page of online banking rather than a step of signing in."""
    return url.startswith(NBC_APP) and not url[len(NBC_APP) :].startswith("auth")


class NationalBank(ZenBankScraper):
    def __init__(self, config: BrowserConfig = BrowserConfig()):
        super().__init__(config=config)
        self._authenticated: bool = False
        self._export_notes: list[str] = []

    # ── BankScraper interface ──────────────────────────────────────────

    def start_auth(self, **kwargs: Any) -> AuthStep:
        credentials = credentials_or_env("nbc", kwargs.get("credentials"))
        self._navigate("open the NBC entry page", NBC_ENTRY)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)
        self._accept_cookies()
        self._open_sign_in_form()
        self._login(credentials.username, credentials.password)

        outcome = self._wait_for_login_outcome()
        if outcome == "signed_in":
            logger.info("NBC let the sign-in through without a code")
            self._authenticated = True
            self._accept_cookies()
            return AuthStep(status="authenticated")
        message = self._otp_notice()
        logger.info("NBC is waiting for the code: %s", message)
        return AuthStep(status="needs_otp", message=message)

    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        if self._authenticated or self._is_signed_in():
            logger.info("Already signed in to NBC — no code to enter")
            self._authenticated = True
            return AuthStep(status="authenticated")
        if not otp_code:
            raise ValueError("The verification code NBC sent is required")

        rejection = self._send_otp(otp_code.strip())
        if rejection is not None:
            # The session stays open so the user can type the code again, or a new one.
            return AuthStep(status="needs_otp", message=rejection)
        self._authenticated = True
        self._accept_cookies()
        return AuthStep(status="authenticated")

    def download_transaction_files(self, *, export_directory: Path, **kwargs: Any) -> list[Path]:
        """One CSV per account, covering ``since_date`` up to today.

        The history tab is set to "Last 12 months" and read until a row older than
        ``since_date`` shows up (or the table has nothing more to load). An older
        ``since_date`` than 12 months back is accepted, but the files then start at the
        oldest row NBC shows, and :meth:`export_notes` says so."""
        self._export_notes = []
        since = _as_date(kwargs.get("since_date"))
        if since is None:
            raise ValueError("since_date is required for the NBC export")
        today = date.today()
        if since > today:
            raise ValueError(f"since_date {since.isoformat()} is in the future")
        if since < today - TWELVE_MONTHS:
            logger.warning(
                "since_date %s is more than 12 months back; NBC's 'Last 12 months' view starts later, "
                "so the export starts at the oldest row it shows",
                since.isoformat(),
            )

        try:
            self._open_transaction_history()
            self._show_last_12_months()
            rows, reached_since = self._read_rows_back_to(since, today=today)
        finally:
            self._sign_out()

        try:
            transactions = keep_since(parse_rows(rows, today=today), since)
        except TransactionParseError as e:
            raise ScraperError(f"Could not read the NBC transaction table: {e}") from e
        logger.info("%d of %d NBC row(s) are on or after %s", len(transactions), len(rows), since.isoformat())
        if not transactions:
            raise ValueError(f"NBC shows no transactions on or after {since.isoformat()}")
        note = coverage_note(transactions, since, reached_since=reached_since, today=today)
        if note:
            logger.warning("%s", note)
            self._export_notes.append(note)

        export_directory = Path(export_directory)
        files = write_account_csvs(transactions, export_directory, export_date=today)
        logger.info("Returning %d NBC file(s): %s", len(files), ", ".join(path.name for path in files))
        return files

    def export_notes(self) -> list[str]:
        return list(self._export_notes)

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        """Single-file view of the export, for callers bound to one path: the per-account
        CSVs bundled into one ZIP when there are several. Nothing over HTTP takes this route."""
        files = self.download_transaction_files(export_directory=export_directory, **kwargs)
        if len(files) == 1:
            return files[0]
        zip_path = Path(export_directory) / _BUNDLE_NAME.format(date=date.today().isoformat())
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, arcname=path.name)
        logger.info("Bundled %d NBC file(s) into %s", len(files), zip_path.name)
        return zip_path

    # ── Signing in ─────────────────────────────────────────────────────

    def _accept_cookies(self) -> None:
        """Accept Didomi's "Respecting your privacy" banner if it shows up. Optional."""
        try:
            accept = self._wait_until(
                "find the NBC cookie banner's Accept button",
                clickable(By.CSS_SELECTOR, ConsentCss.ACCEPT),
                DelaySeconds.COOKIE_PROMPT,
                timeout_log_level=logging.INFO,
            )
        except TimeoutException:
            logger.info("No NBC cookie banner found — continuing")
            return
        self._click("accept the NBC cookie banner", accept)

    def _open_sign_in_form(self) -> None:
        """Get past "You can't access this page from this link" to the sign-in form.

        A direct visit usually lands on that page; its "Back to sign-in" button opens the
        form. When the form is already showing, there is nothing to click."""
        try:
            locator, element = self._wait_until(
                "find the NBC sign-in form or the 'Back to sign-in' button",
                lambda _driver: (
                    self._first_displayed_match(((By.CSS_SELECTOR, ConnectionElementCss.USERNAME),))
                    or self._first_displayed_match(_BACK_TO_SIGN_IN_LOCATORS)
                ),
                DelaySeconds.ENTRY_PAGE,
                screenshot_name="nbc_entry_page_timeout",
            )
        except TimeoutException as e:
            raise ScraperError("NBC's entry page showed neither the sign-in form nor 'Back to sign-in'") from e
        if locator == ConnectionElementCss.USERNAME:
            logger.info("The NBC sign-in form is already showing")
            return
        logger.info("NBC showed the access-error page — clicking 'Back to sign-in' (matched %s)", locator)
        self._click("click 'Back to sign-in' on the NBC access-error page", element)
        try:
            self._wait_until(
                "wait for the NBC sign-in form",
                visible(By.CSS_SELECTOR, ConnectionElementCss.USERNAME),
                DelaySeconds.PAGE_TIMEOUT,
                screenshot_name="nbc_sign_in_form_timeout",
            )
        except TimeoutException as e:
            raise ScraperError("NBC never showed the sign-in form after 'Back to sign-in'") from e
        self._accept_cookies()

    def _login(self, username: SecretString, password: SecretString) -> None:
        username_field = self._find_element("enter the NBC email ID", By.CSS_SELECTOR, ConnectionElementCss.USERNAME)
        self._send_keys_verified("enter the NBC email ID", username_field, username.reveal(), sensitive=True)
        password_field = self._find_element("enter the NBC password", By.CSS_SELECTOR, ConnectionElementCss.PASSWORD)
        self._send_keys_verified("enter the NBC password", password_field, password.reveal(), sensitive=True)
        submit = self._find_element("click the NBC Sign in button", By.CSS_SELECTOR, ConnectionElementCss.SUBMIT)
        self._click("click the NBC Sign in button", submit)

    def _wait_for_login_outcome(self) -> str:
        """``"otp"`` (the code page) or ``"signed_in"``; raises when NBC turns the login down."""

        def outcome(_driver: Any) -> str | None:
            if self._first_displayed_match(((By.CSS_SELECTOR, ConnectionElementCss.OTP_INPUT),)):
                return "otp"
            if self._is_signed_in():
                return "signed_in"
            error = self._login_error()
            if error:
                return f"error:{error}"
            return None

        try:
            result = self._wait_until(
                "see what NBC does with the sign-in",
                outcome,
                DelaySeconds.LOGIN_OUTCOME,
                screenshot_name="nbc_login_outcome_timeout",
            )
        except TimeoutException as e:
            raise ScraperError("NBC showed neither the code page nor online banking after Sign in") from e
        if result.startswith("error:"):
            self._save_screenshot("nbc_login_refused")
            raise ValueError(f"NBC refused the sign-in: {result[len('error:') :]}")
        return result

    def _login_error(self) -> str | None:
        """The error text the sign-in form shows, while it is still on screen."""
        if not self._first_displayed_match(((By.CSS_SELECTOR, ConnectionElementCss.PASSWORD),)):
            return None
        match = self._first_displayed_match(_LOGIN_ERROR_LOCATORS)
        if match is None:
            return None
        text = " ".join(match[1].text.split())
        return text or None

    def _otp_notice(self) -> str:
        """Where NBC sent the code, in one line for the web UI."""
        try:
            sentence = self._find_element(
                "read how NBC sent the code", By.CSS_SELECTOR, ConnectionElementCss.OTP_CHANNEL
            ).text
        except ScraperError:
            logger.info("NBC's code page does not say how the code was sent")
            sentence = ""
        try:
            alert = self._find_element(
                "read where NBC sent the code", By.CSS_SELECTOR, ConnectionElementCss.OTP_DESTINATION
            ).text
        except ScraperError:
            logger.info("NBC's code page does not say where the code was sent")
            alert = ""
        if not sentence and not alert:
            return OTP_FALLBACK
        return describe_otp(sentence, alert)

    def _send_otp(self, otp_code: str) -> str | None:
        """Type and confirm the code. Returns NBC's message when it rejects the code."""
        otp_field = self._find_element(
            "enter the NBC verification code", By.CSS_SELECTOR, ConnectionElementCss.OTP_INPUT
        )
        # A retry after a rejected code finds the old one still in the field.
        if otp_field.get_attribute("value"):
            self._clear_field("enter the NBC verification code", otp_field)
        self._send_keys("enter the NBC verification code", otp_field, otp_code, sensitive=True)
        confirm = self._find_element("click the NBC Confirm button", By.CSS_SELECTOR, ConnectionElementCss.OTP_SUBMIT)
        self._click("click the NBC Confirm button", confirm)
        confirmed_at = time.monotonic()

        def outcome(_driver: Any) -> str | None:
            if self._is_signed_in():
                return "signed_in"
            if self._first_displayed_match(((By.CSS_SELECTOR, ConnectionElementCss.PASSWORD),)):
                return "back_to_sign_in"
            if time.monotonic() - confirmed_at < DelaySeconds.OTP_ERROR_GRACE:
                return None
            error = self._first_displayed_match(((By.CSS_SELECTOR, ConnectionElementCss.OTP_ERROR),))
            if error and " ".join(error[1].text.split()):
                return "rejected:" + " ".join(error[1].text.split())
            return None

        try:
            result = self._wait_until(
                "see what NBC does with the code",
                outcome,
                DelaySeconds.OTP_OUTCOME,
                screenshot_name="nbc_otp_outcome_timeout",
            )
        except TimeoutException as e:
            raise ScraperError("NBC never got past the code page after Confirm") from e
        if result == "back_to_sign_in":
            self._save_screenshot("nbc_otp_back_to_sign_in")
            raise ScraperError("NBC sent the session back to the sign-in page after the code; start the login again")
        if result.startswith("rejected:"):
            reason = result[len("rejected:") :]
            logger.warning("NBC rejected the code: %s", reason)
            return f"NBC did not accept the code ({reason}). Enter it again, or the new code if NBC sent one."
        logger.info("Signed in to NBC")
        return None

    def _is_signed_in(self) -> bool:
        try:
            return is_signed_in_url(self.driver.current_url) and not self._first_displayed_match(
                ((By.CSS_SELECTOR, ConnectionElementCss.PASSWORD),)
            )
        except ScraperError:
            return False

    # ── Transaction history ────────────────────────────────────────────

    def _open_transaction_history(self) -> None:
        """Dashboard → "See all my accounts" → the Transaction history tab."""
        try:
            see_all = self._wait_until(
                "find 'See all my accounts' on the NBC dashboard",
                clickable(By.XPATH, AccountsElementXpath.SEE_ALL_ACCOUNTS),
                DelaySeconds.PAGE_TIMEOUT,
                timeout_log_level=logging.WARNING,
            )
        except TimeoutException:
            self._save_screenshot("nbc_see_all_accounts_missing")
            logger.warning("No 'See all my accounts' button on the NBC dashboard; opening %s directly", NBC_ACCOUNTS)
            self._navigate("open the NBC accounts page", NBC_ACCOUNTS)
        else:
            self._click("click 'See all my accounts' on the NBC dashboard", see_all)
        try:
            self._wait_until(
                "wait for the NBC accounts page",
                url_contains("/accounts"),
                DelaySeconds.PAGE_TIMEOUT,
                screenshot_name="nbc_accounts_page_timeout",
            )
            tab = self._wait_until(
                "find the NBC Transaction history tab",
                clickable(By.XPATH, AccountsElementXpath.TRANSACTION_HISTORY_TAB),
                DelaySeconds.PAGE_TIMEOUT,
                screenshot_name="nbc_transaction_tab_timeout",
            )
        except TimeoutException as e:
            raise ScraperError("NBC's accounts page never showed the Transaction history tab") from e
        self._click("open the NBC Transaction history tab", tab)
        try:
            self._wait_until(
                "wait for the NBC Transaction history tab to open",
                present(By.XPATH, AccountsElementXpath.SELECTED_TRANSACTION_HISTORY_TAB),
                DelaySeconds.PAGE_TIMEOUT,
                screenshot_name="nbc_transaction_tab_not_selected",
            )
            self._wait_until(
                "wait for the NBC Period filter",
                present(By.CSS_SELECTOR, TransactionsCss.PERIOD),
                DelaySeconds.PAGE_TIMEOUT,
                screenshot_name="nbc_period_filter_timeout",
            )
        except TimeoutException as e:
            raise ScraperError("NBC's Transaction history tab never opened") from e

    def _show_last_12_months(self) -> None:
        period = self._find_element("choose the NBC Period filter", By.CSS_SELECTOR, TransactionsCss.PERIOD)
        self._select_option("choose 'Last 12 months' in the NBC Period filter", period, LAST_12_MONTHS)
        apply = self._find_element("click Apply on the NBC transaction filter", By.CSS_SELECTOR, TransactionsCss.APPLY)
        self._click("click Apply on the NBC transaction filter", apply)
        self._wait_for_table("load the last 12 months of NBC transactions")

    def _wait_for_table(self, description: str) -> int:
        """Wait until the row count has held still for a moment with no spinner, and return it."""
        state: dict[str, Any] = {"count": -1, "since": time.monotonic()}

        def settled(_driver: Any) -> bool | None:
            count = self._row_count()
            now = time.monotonic()
            if count != state["count"] or self._first_displayed_match(((By.CSS_SELECTOR, TransactionsCss.LOADING),)):
                state["count"], state["since"] = count, now
                return None
            return True if now - state["since"] >= DelaySeconds.TABLE_SETTLE else None

        try:
            self._wait_until(description, settled, DelaySeconds.TABLE_RELOAD, screenshot_name="nbc_table_timeout")
        except TimeoutException as e:
            raise ScraperError(f"NBC's transaction table never settled while trying to {description}") from e
        logger.info("The NBC transaction table shows %d row(s)", state["count"])
        return int(state["count"])

    def _row_count(self) -> int:
        return int(self.driver.execute_script(_ROW_COUNT_SCRIPT) or 0)

    def _read_rows(self) -> list[dict[str, Any]]:
        logger.info("Reading the rows of the NBC transaction table")
        try:
            raw = self.driver.execute_script(_ROWS_SCRIPT)
            rows = json.loads(raw or "[]")
        except (ScraperError, ValueError) as e:
            raise ScraperError("Failed while reading the rows of the NBC transaction table") from e
        return list(rows)

    def _read_rows_back_to(self, since: date, *, today: date) -> tuple[list[dict[str, Any]], bool]:
        """Read the table, loading more rows until one is older than ``since`` or none come.

        Returns the rows, and whether a row older than ``since`` was reached (otherwise the
        rows may start after ``since``). The saved page showed no paging, so both of the likely
        kinds are handled: a "show more" button is clicked, and failing that the page is
        scrolled to the bottom in case it loads more as it goes."""
        rows = self._read_rows()
        for round_number in range(1, DelaySeconds.MAX_LOAD_MORE_ROUNDS + 1):
            oldest = _oldest_date(rows, today=today)
            logger.info(
                "NBC table round %d: %d row(s), oldest %s",
                round_number,
                len(rows),
                oldest.isoformat() if oldest else "none",
            )
            if oldest is not None and oldest < since:
                logger.info("The NBC table reaches past %s — no more rows needed", since.isoformat())
                return rows, True
            load_more = self._first_displayed_match(((By.XPATH, AccountsElementXpath.LOAD_MORE),))
            if load_more is not None:
                self._click("load more NBC transactions", load_more[1])
            else:
                logger.info("No 'show more' button under the NBC table; scrolling to the bottom")
                self.driver.execute_script(_SCROLL_TO_BOTTOM_SCRIPT)
            before = len(rows)
            if not self._more_rows_started(before):
                logger.info("The NBC table loaded nothing more; it holds %d row(s)", before)
                return rows, False
            self._wait_for_table("load more NBC transactions")
            rows = self._read_rows()
            if len(rows) <= before:
                logger.info("The NBC table loaded nothing more; it holds %d row(s)", len(rows))
                return rows, False
        logger.warning(
            "Stopped loading NBC transactions after %d rounds with %d row(s)",
            DelaySeconds.MAX_LOAD_MORE_ROUNDS,
            len(rows),
        )
        return rows, False

    def _more_rows_started(self, before: int) -> bool:
        """Whether, after "show more" or a scroll, the table starts loading: its row count rises
        past ``before`` or its spinner shows. Without this, a slow first response would look
        like a table that had settled with nothing more to give."""
        try:
            self._wait_until(
                "see the NBC table start loading more rows",
                lambda _driver: (
                    self._row_count() > before
                    or self._first_displayed_match(((By.CSS_SELECTOR, TransactionsCss.LOADING),)) is not None
                    or None
                ),
                DelaySeconds.LOAD_MORE_START,
                timeout_log_level=logging.INFO,
            )
        except TimeoutException:
            return False
        return True

    def _sign_out(self) -> None:
        """Sign out of NBC. Best effort: the browser is closed right after either way."""
        try:
            match = self._first_displayed_match(((By.XPATH, AccountsElementXpath.SIGN_OUT),))
            if match is None:
                logger.info("No NBC Sign out button on screen — skipping sign-out")
                return
            self._click("sign out of NBC", match[1])
            self._wait_until(
                "wait for NBC to sign out",
                lambda driver: not is_signed_in_url(driver.current_url) or None,
                DelaySeconds.SIGN_OUT,
                timeout_log_level=logging.INFO,
            )
        except ScraperError as e:
            logger.warning("Could not sign out of NBC cleanly (%s); the browser is closed anyway", e)
        self._authenticated = False


def _as_date(value: datetime | date | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def _oldest_date(rows: list[dict[str, Any]], *, today: date) -> date | None:
    """The oldest readable date among ``rows``; unreadable ones are left for the parser to report."""
    dates: list[date] = []
    for row in rows:
        try:
            dates.append(parse_date(str(row.get("date") or ""), today=today))
        except TransactionParseError:
            continue
    return min(dates) if dates else None
