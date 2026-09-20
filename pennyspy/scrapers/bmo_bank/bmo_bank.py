from __future__ import annotations

import csv
import json
import logging
import re
import secrets
import zipfile
from collections.abc import Callable
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Final, Literal, cast

import requests

from pennyspy.scrapers.base import AuthStep, ZenBankScraper
from pennyspy.scrapers.bmo_bank.connection_element_id import PHONE_METHOD_WORDS, ConnectionElementId
from pennyspy.scrapers.bmo_bank.delay_seconds import DelaySeconds
from pennyspy.scrapers.bmo_bank.get_default_filename import get_default_filename
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate
from pennyspy.scrapers.get_required_env_var import SecretString, get_required_env_var
from pennyspy.scrapers.scraper import BrowserConfig
from pennyspy.scrapers.zen_scraper import (
    By,
    ElementHandle,
    ScraperError,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
    clickable,
    invisible,
    present,
    url_to_be,
)

BMO_LOGIN_URL: Final[str] = "https://www1.bmo.com/banking/digital/login"
BMO_SUCCESS_URL: Final[str] = "https://www1.bmo.com/banking/digital/accounts"
BMO_DOWNLOAD_URL: Final[str] = "https://www1.bmo.com/banking/services/accountdetails/downloadCCTransactions"
BMO_ACCOUNT_DETAILS_BASE: Final[str] = "https://www1.bmo.com/banking/digital/account-details"

# The transaction table header labels that carry an amount, in the order we prefer to read them.
# Bank accounts split the amount across "Money out"/"Money in"; credit cards use a single
# "Money in/out" column. We take the first non-empty amount cell across whichever are present.
_AMOUNT_HEADERS: Final[frozenset[str]] = frozenset({"Money in/out", "Money out", "Money in"})
# "1-20 of 126" style pagination range label.
_RANGE_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s+of\s+(\d[\d,]*)")
# Trailing UUID of an /account-details/{ba,cc}/{uuid} href.
_ACCOUNT_HREF_RE: Final[re.Pattern[str]] = re.compile(r"/account-details/(?:ba|cc)/([0-9a-fA-F-]+)")
# Hard cap on pagination so a pager that stops advancing can never spin the request forever.
_MAX_PAGINATION_PAGES: Final[int] = 60
# Sign-in button locators, most- to least-specific (see BMOBank._find_sign_in_button).
_SIGN_IN_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.SIGN_IN,
    ConnectionElementId.SIGN_IN_ARIA,
    ConnectionElementId.SIGN_IN_TEXT,
)
# Cap on the account-name part of a multi-account filename, so a long side-nav label
# (name, type and masked number) can't grow the path past what the filesystem accepts.
_MAX_ACCOUNT_SLUG_CHARS: Final[int] = 40

# ── 2FA locator chains ───────────────────────────────────────────────────────────────
# Every step of BMO's OTP flow is looked for through several locators in turn, most- to
# least-specific: the stable ``name`` attribute first, then the component/ARIA structure, then
# the visible text. None of them keys on an id — BMO stamps a fresh UUID on each render — and
# each click is verified against the DOM afterwards rather than assumed to have landed.
_MFA_SCREEN_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.MFA_METHOD_STEP,
    ConnectionElementId.OTP_STEP,
    ConnectionElementId.MFA_NEXT_BUTTON,
)
_MFA_PHONE_CHOICE_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.MFA_PHONE_RADIO_LABEL,
    ConnectionElementId.MFA_PHONE_RADIO_VALUE_LABEL,
    ConnectionElementId.MFA_PHONE_RADIO_LABEL_ANYWHERE,
    ConnectionElementId.MFA_PHONE_RADIO_VALUE,
    ConnectionElementId.MFA_CONTACT_RADIO_LABEL,
    ConnectionElementId.MFA_CONTACT_RADIO,
)
_MFA_AGREE_CHECKBOX_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.MFA_AGREE_CHECKBOX_LABEL,
    ConnectionElementId.MFA_AGREE_CHECKBOX_LABEL_BY_ID,
    ConnectionElementId.MFA_AGREE_CHECKBOX_LABEL_BY_TEXT,
)
_MFA_SEND_CODE_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.MFA_SEND_CODE,
    ConnectionElementId.MFA_SEND_CODE_BY_NAME,
    ConnectionElementId.MFA_SEND_CODE_BY_TEXT,
)
_OTP_INPUT_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.OTP_INPUT,
    ConnectionElementId.OTP_INPUT_BY_NAME,
    ConnectionElementId.OTP_INPUT_BY_ID,
    ConnectionElementId.OTP_INPUT_BY_FORM_CONTROL,
    ConnectionElementId.OTP_INPUT_NUMERIC,
)
_MFA_CONFIRM_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.MFA_CONFIRM,
    ConnectionElementId.MFA_CONFIRM_BY_NAME,
    ConnectionElementId.MFA_CONFIRM_BY_TEXT,
)
_MFA_CONTINUE_LOCATORS: Final[tuple[str, ...]] = (
    ConnectionElementId.MFA_CONTINUE,
    ConnectionElementId.MFA_CONTINUE_BY_TEXT,
)
# How long a click gets to show up in the DOM before it is treated as one that never landed.
_CLICK_CONFIRM_SECONDS: Final[float] = 2.0
# A delivery option BMO masks rather than spells out: "••• - ••• - 7770", "xxx-xxx-7770".
_MASKED_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"[•*x·]{2,}[\s\-–—.]*[•*x·]{2,}", re.IGNORECASE)
# Reads the delivery option the OTP form currently has selected, as its label text (falling back
# to the radio's value). The design system hides the real radio behind its label, so the checked
# state has to come from the live DOM property; it is never written back into the markup.
_JS_SELECTED_OTP_METHOD: Final[str] = r"""
const radio = document.querySelector("input[type='radio']:checked");
if (!radio) return null;
const label = radio.labels && radio.labels.length ? radio.labels[0].innerText : "";
return (label || radio.value || "selected").replace(/\s+/g, " ").trim();
"""

logger = logging.getLogger(__name__)


def _locator_name(locator: str) -> str:
    """A locator's constant name, for logs: the case-folding XPaths run to hundreds of characters."""
    try:
        return ConnectionElementId(locator).name
    except ValueError:
        return locator


def _is_phone_delivery_method(label: str) -> bool:
    """Whether a BMO delivery option sends the code to a phone.

    Matched on what the option says, because BMO has worded it differently between builds and
    offers other methods (email today, an app prompt tomorrow) in the same list. An option that
    names an address is an email one however it is worded; an option that shows nothing but a
    masked number is a phone one even when the wording is new."""
    lowered = label.casefold()
    if "@" in lowered:
        return False
    if any(word in lowered for word in PHONE_METHOD_WORDS):
        return True
    return bool(_MASKED_NUMBER_RE.search(label))


class BMOBank(ZenBankScraper):
    def __init__(self, config: BrowserConfig = BrowserConfig()):
        super().__init__(config=config)
        self.cookies: list[dict] | None = None
        self._account_uuids: list[str] = []
        # UUID -> full /account-details/{ba,cc}/{uuid} URL, and UUID -> display name. Both are
        # populated from the side nav the first time any account is resolved, so a multi-account
        # session probes at most once regardless of account type ordering.
        self._account_urls: dict[str, str] = {}
        self._account_names: dict[str, str] = {}
        self._user_agent: str = self.driver.execute_script("return navigator.userAgent")
        self._authenticated: bool = False

    # ── BankScraper interface ──────────────────────────────────────────

    def start_auth(self, **kwargs: Any) -> AuthStep:
        account_uuids: list[str] = list(kwargs["account_uuids"])
        assert account_uuids, "At least one account UUID is required"
        self._account_uuids = account_uuids
        username = get_required_env_var("PENNYSPY_BMOU")
        password = get_required_env_var("PENNYSPY_BMOPP")

        logger.info("Navigating to BMO login page")
        self._navigate("open BMO login page", BMO_LOGIN_URL)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)

        self._login(username, password)

        outcome = self._wait_for_2fa_or_success()
        if outcome == "2fa":
            self._handle_2fa_initiation()
            logger.info("2FA initiation complete — waiting for OTP from user")
            return AuthStep(status="needs_otp", message="Enter the OTP code sent to your phone")
        else:
            logger.info("Logged in without 2FA")
            self._authenticated = True
            return AuthStep(status="authenticated")

    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        if self._authenticated:
            return AuthStep(status="authenticated")
        assert otp_code is not None, "OTP code is required for BMO 2FA"
        self._complete_2fa(otp_code)
        self._authenticated = True
        return AuthStep(status="authenticated")

    def download_transaction_files(self, *, export_directory: Path, **kwargs: Any) -> list[Path]:
        """One file per requested account, handed back as they were downloaded.

        BMO downloads per account, so a multi-account scrape produces several files. They are
        returned as themselves rather than packed into an archive, so the caller can serve each
        one separately instead of leaving the user an archive to unpack.
        """
        results = self._download_per_account(export_directory=Path(export_directory), **kwargs)
        logger.info(
            "Returning %d BMO download(s): %s",
            len(results),
            ", ".join(path.name for _, path in results),
        )
        return [path for _, path in results]

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        """Single-file view of the download, for callers bound to one path.

        Bundling is only a way to satisfy that one-path contract — nothing over HTTP takes this
        route, so the web UI never receives a ZIP. Prefer :meth:`download_transaction_files`,
        which hands back the per-account files themselves.
        """
        export_directory = Path(export_directory)
        results = self._download_per_account(export_directory=export_directory, **kwargs)
        if len(results) == 1:
            return results[0][1]
        return self._bundle_files(results, export_directory)

    def _download_per_account(self, *, export_directory: Path, **kwargs: Any) -> list[tuple[str, Path]]:
        """Download every requested account's transactions as ``(uuid, path)`` pairs."""
        assert self._account_uuids, "No account UUIDs available for this session"
        app_type: AppType = kwargs["app_type"]
        from_date = kwargs.get("from_date")
        statement_date: StatementDate | None = kwargs.get("statement_date")

        export_directory.mkdir(parents=True, exist_ok=True)

        single_account = len(self._account_uuids) == 1

        results: list[tuple[str, Path]] = []
        for account_uuid in self._account_uuids:
            logger.info("Downloading transactions for account %s", account_uuid)
            # Give each account its own directory so identically-named default
            # download files don't overwrite one another before they are labelled
            # apart. A single account writes straight to export_directory.
            account_dir = export_directory if single_account else export_directory / account_uuid
            if from_date is not None:
                # Web scraping path — uses the live browser, no cookies needed
                file_path = self._parse_transactions_from_web(
                    account_uuid=account_uuid,
                    from_date=from_date,
                    export_directory=account_dir,
                )
            else:
                # API path — downloadCCTransactions is credit-card-only, so reject bank accounts
                # up front with a clear message instead of letting the API return an opaque error.
                account_url = self._resolve_account_url(account_uuid)
                if "/account-details/ba/" in account_url:
                    raise ValueError(
                        f"BMO account {account_uuid} ({self._account_names.get(account_uuid, 'bank account')}) "
                        "is a bank account; the CSV/QFX download (statement_date) path supports credit cards "
                        "only. Use the web-parsing path (from_date) for bank accounts."
                    )
                # capture cookies once (shared across accounts in the session)
                if self.cookies is None:
                    self._capture_cookies(account_uuid)
                assert statement_date is not None, "statement_date is required for the API download path"
                file_path = self._download_transactions_via_api(
                    account_uuid=account_uuid,
                    app_type=app_type,
                    statement_date=statement_date,
                    export_directory=account_dir,
                )
            if not single_account:
                file_path = self._label_with_account(file_path, account_uuid)
            results.append((account_uuid, file_path))

        return results

    def _label_with_account(self, path: Path, account_uuid: str) -> Path:
        """Rename ``path`` so its name says which account it holds.

        BMO names every account's download the same thing, so files from a multi-account scrape
        would be indistinguishable once they are served side by side — and would overwrite each
        other wherever they are collected into one directory. The display name is what the user
        recognises, but two accounts can share one, so the UUID's leading block is appended to
        keep the name unique. Renaming is best effort: a failure here must not lose a download
        that already succeeded.
        """
        name = self._account_names.get(account_uuid, "")
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:_MAX_ACCOUNT_SLUG_CHARS].strip("-")
        short_uuid = account_uuid.split("-")[0][:8] or "account"
        label = f"{slug}-{short_uuid}" if slug else short_uuid

        target = path.with_name(f"{path.stem}_{label}{path.suffix}")
        if target == path:
            return path
        try:
            path.replace(target)
        except OSError:
            logger.warning("Could not rename %s to %s; serving it under its original name", path, target.name)
            return path
        return target

    def _bundle_files(self, results: list[tuple[str, Path]], export_directory: Path) -> Path:
        """Bundle multiple per-account transaction files into a single ZIP archive.

        Each file is namespaced under its account UUID so downloads with identical
        default filenames don't collide inside the archive.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        zip_path = export_directory / f"bmo_transactions_{today_str}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for account_uuid, file_path in results:
                archive.write(file_path, arcname=f"{account_uuid}/{file_path.name}")
        logger.info("Bundled %d account files into %s", len(results), zip_path)
        return zip_path

    # ── Internal implementation ────────────────────────────────────────

    def _complete_2fa(self, otp_code: str) -> None:
        """Type the code into BMO's "Enter your code" step and confirm it.

        Does NOT capture cookies or quit the driver."""
        logger.info("Entering OTP code")
        try:
            locator, otp_field = self._wait_for_any(
                "find the visible BMO OTP input field",
                _OTP_INPUT_LOCATORS,
                DelaySeconds.MFA_STEP_TIMEOUT,
            )
        except TimeoutException as e:
            raise TimeoutException("Couldn't find OTP input field while completing BMO 2FA") from e
        logger.info("Found the BMO OTP input field via %s", _locator_name(locator))
        self._send_keys_verified("enter BMO OTP code", otp_field, otp_code, sensitive=True)

        self._leave_this_device_untrusted()

        # human=True: the post-OTP confirm is the most heavily fingerprinted click of the login —
        # give it real pointer movement + dwell rather than a bare synthetic click.
        self._click_first_match(
            "the BMO OTP confirm button",
            _MFA_CONFIRM_LOCATORS,
            DelaySeconds.MFA_STEP_TIMEOUT,
        )
        self._finish_post_otp_navigation()

    def _leave_this_device_untrusted(self) -> None:
        """Make sure BMO's "Trust this device" box is clear before the code is confirmed.

        Ticking it registers this machine with BMO and skips 2FA on later logins, which is not
        the scraper's call to make — so the box is never clicked, only read back, and unticked
        again if a build ever renders it pre-selected."""
        state = self._checkbox_state(ConnectionElementId.MFA_TRUST_DEVICE_INPUT)
        if state is None:
            logger.info("No 'Trust this device' checkbox on this BMO screen")
            return
        if not state:
            logger.info("Leaving BMO's 'Trust this device' checkbox unticked")
            return

        logger.warning("BMO rendered 'Trust this device' pre-ticked; unticking it before confirming")
        if self._click_until(
            "untick BMO's 'Trust this device' checkbox",
            (ConnectionElementId.MFA_TRUST_DEVICE_LABEL,),
            lambda: self._checkbox_state(ConnectionElementId.MFA_TRUST_DEVICE_INPUT) is not True,
        ):
            return
        logger.error(
            "Could not untick BMO's 'Trust this device' checkbox; this device may be registered "
            "with BMO. Continuing with the login."
        )

    def _finish_post_otp_navigation(self) -> None:
        """Wait out whatever BMO puts between CONFIRM and the accounts page.

        Older builds ended on a CONTINUE interstitial; the current one redirects straight to the
        accounts page, so CONTINUE is clicked only when it actually appears. A rejected code shows
        up as an inline error, which is logged as soon as it appears and repeated in the timeout so
        the failure names the reason instead of just the elapsed seconds."""
        logger.info("Waiting for BMO to accept the OTP code")
        logged_errors: set[str] = set()

        def reached_outcome(driver) -> str | None:
            if driver.current_url == BMO_SUCCESS_URL:
                return "success"
            if self._first_displayed(_MFA_CONTINUE_LOCATORS) is not None:
                return "continue"
            message = self._extract_otp_error()
            if message and message not in logged_errors:
                logged_errors.add(message)
                logger.error("BMO OTP error displayed: %s", message)
            return None

        try:
            outcome = self._wait_until(
                "reach the BMO accounts page or a CONTINUE step after the OTP code",
                reached_outcome,
                DelaySeconds.LOGIN_SUCCESS_TIMEOUT,
                screenshot_name="bmo_post_2fa_timeout",
            )
        except TimeoutException as e:
            message = self._extract_otp_error()
            if message:
                raise TimeoutException(f"BMO did not accept the OTP code: {message}") from e
            raise

        if outcome == "continue":
            self._click_first_match(
                "the BMO continue button after the OTP code",
                _MFA_CONTINUE_LOCATORS,
                DelaySeconds.MFA_STEP_TIMEOUT,
            )

        logger.info("Waiting for post-2FA redirect to %s", BMO_SUCCESS_URL)
        self._wait_until(
            f"complete BMO post-2FA redirect to {BMO_SUCCESS_URL}",
            url_to_be(BMO_SUCCESS_URL),
            DelaySeconds.LOGIN_SUCCESS_TIMEOUT,
            screenshot_name="bmo_post_2fa_redirect_timeout",
        )

    def _download_transactions_via_api(
        self,
        account_uuid: str,
        app_type: AppType,
        statement_date: StatementDate,
        export_directory: Path | str,
    ) -> Path:
        assert self.cookies is not None, "Cookies have not been captured yet."

        export_directory = Path(export_directory)
        export_directory.mkdir(parents=True, exist_ok=True)

        session = requests.Session()
        cookie_map: dict[str, str] = {}
        for cookie in self.cookies:
            session.cookies.set(cookie["name"], cookie["value"])
            cookie_map[cookie["name"]] = cookie["value"]

        xsrf_token = cookie_map.get("XSRF-TOKEN", "")
        mfa_token = cookie_map.get("PMData", "")
        request_id = f"REQ_{secrets.token_hex(8)}"
        now_time = datetime.now()
        client_date = now_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

        headers = {
            "Content-Type": "application/json",
            "X-ChannelType": "OLB",
            "x_channeltype": "OLB",
            "X-XSRF-TOKEN": xsrf_token,
            "X-UI-Session-ID": "0.0.1",
            "X-App-Version": "session-id",
            "X-App-Current-Path": f"/banking/digital/account-details/cc/{account_uuid}",
            "X-Request-ID": request_id,
            "X-Original-Request-Time": now_time.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Referer": (f"https://www1.bmo.com/banking/digital/account-details/cc/{account_uuid}?modal=transactions"),
        }

        body = {
            "DownloadMCAccountDetailsRq": {
                "HdrRq": {
                    "ver": "1.0",
                    "channelType": "OLB",
                    "appName": "OLB",
                    "hostName": "BDBN-HostName",
                    "clientDate": client_date,
                    "rqUID": request_id,
                    "clientSessionID": "session-id",
                    "userAgent": self._user_agent,
                    "clientIP": "127.0.0.1",
                    "mfaDeviceToken": mfa_token,
                },
                "BodyRq": {
                    "accountIndex": "0",
                    "statementDate": statement_date.value,
                    "appType": app_type.value,
                },
            }
        }

        logger.info(
            "Posting download request (app_type=%s, statement_date=%s)",
            app_type,
            statement_date,
        )
        response = session.post(BMO_DOWNLOAD_URL, json=body, headers=headers)
        assert response.status_code == HTTPStatus.OK, (
            f"Download failed with status {response.status_code}: {response.text[:200]}"
        )

        payload = response.json()
        body_rs = payload["DownloadCCTransactionsRs"]["BodyRs"]
        if errors := body_rs.get("errorList"):
            messages = "; ".join(f"[{e.get('code', 'UNKNOWN')}] {e.get('errorMessage', 'No message')}" for e in errors)
            raise ValueError(f"API returned errors: {messages}")
        file_content: str = body_rs["pfmFile"]

        filename = self._parse_filename_from_header(body_rs.get("header", ""))
        if not filename:
            filename = get_default_filename(app_type)

        file_path = export_directory / filename
        file_path.write_text(file_content, encoding="utf-8")
        logger.info("Transaction file saved: %s", file_path)
        return file_path

    def _parse_transactions_from_web(
        self,
        account_uuid: str,
        from_date: datetime,
        export_directory: Path,
    ) -> Path:
        export_directory.mkdir(parents=True, exist_ok=True)

        account_url = self._resolve_account_url(account_uuid)
        logger.info("Navigating to account details page for web parsing")
        self._navigate("open BMO account details page for web parsing", account_url)

        self._wait_until(
            "load BMO transaction rows",
            present(By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_ROW_INTERACTIVE),
            DelaySeconds.PAGE_TIMEOUT,
            screenshot_name="bmo_transactions_load_timeout",
        )
        self._set_max_rows_per_page()

        all_transactions: list[tuple[datetime, str, float]] = []
        reached_date_limit = False

        for page_index in range(1, _MAX_PAGINATION_PAGES + 1):
            try:
                page_transactions = self._parse_posted_transactions_from_page()
            except StaleElementReferenceException:
                sleep(1)
                page_transactions = self._parse_posted_transactions_from_page()
            for txn_date, desc, amount in page_transactions:
                if txn_date < from_date:
                    reached_date_limit = True
                    continue
                all_transactions.append((txn_date, desc, amount))

            page_range = self._read_range_label()
            logger.info(
                "BMO page %d (%s): %d txns on page, %d kept total, reached_date_limit=%s",
                page_index,
                self._format_range(page_range),
                len(page_transactions),
                len(all_transactions),
                reached_date_limit,
            )

            if reached_date_limit:
                break
            # The range label ("end of total") is the authoritative end-of-data signal; fall back
            # to the disabled/absent Next button below when it can't be read.
            if page_range is not None and page_range[1] >= page_range[2]:
                break

            try:
                next_btn = self._find_element(
                    "find BMO pagination next button",
                    By.CSS_SELECTOR,
                    ConnectionElementId.PAGINATION_NEXT_BUTTON,
                )
            except Exception:
                logger.info("BMO pagination next button not found; stopping pagination")
                break

            if next_btn.get_attribute("disabled") is not None:
                break

            self._click("click BMO pagination next button", next_btn)
            if not self._wait_for_page_advance(page_range):
                # The Next click did not advance the pager: refuse to loop on a stale page.
                raise TimeoutException(
                    f"BMO pagination stalled on page {page_index} "
                    f"({self._format_range(page_range)}) — Next did not advance the table"
                )
        else:
            raise TimeoutException(
                f"BMO pagination exceeded the {_MAX_PAGINATION_PAGES}-page cap "
                "without reaching the end of the transaction list"
            )

        if not all_transactions:
            raise ValueError("No transactions were found for the selected date range.")

        today_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"bmo_web_transactions_{today_str}.csv"
        file_path = export_directory / filename

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Description", "Amount"])
            for txn_date, desc, amount in all_transactions:
                writer.writerow([txn_date.strftime("%Y-%m-%d"), desc, amount])

        logger.info("Web-parsed transactions saved: %s (%d rows)", file_path, len(all_transactions))
        return file_path

    def _parse_amount_from_web(self, text: str, *, date_text: str = "", description: str = "") -> float:
        cleaned = text.replace("\n", "").strip()
        sign = -1 if "-" in cleaned else 1
        number = re.search(r"[\d,.]+", cleaned)
        if not number:
            raise ValueError(f"Invalid amount {text!r} for transaction on {date_text!r} ({description!r})")
        value = float(number.group().replace(",", ""))
        return sign * value

    def _extract_table_from_page(self) -> dict[str, Any] | None:
        """Read the active tab's transaction table (headers + per-row cell text) in one DOM pass.

        Done in JS (rather than per-cell element handles) because it is both far faster over CDP
        and immune to the stale-handle problems the old Selenium nested-find approach hit during
        pagination. Everything is scoped to the visible tab panel so a hidden, still-mounted
        account page can't contribute rows.
        """
        scope = json.dumps(str(ConnectionElementId.ACTIVE_TAB_PANEL))
        rows_xpath = json.dumps(str(ConnectionElementId.TRANSACTION_ROWS))
        headers_xpath = json.dumps(str(ConnectionElementId.TRANSACTION_HEADERS))
        script = f"""
        const root = document.querySelector({scope});
        if (!root) return null;
        const text = (node) => ((node ? node.textContent : '') || '').trim();
        const headerNodes = document.evaluate({headers_xpath}, root, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const headers = [];
        for (let i = 0; i < headerNodes.snapshotLength; i++) {{
            headers.push(text(headerNodes.snapshotItem(i)));
        }}
        const rowNodes = document.evaluate({rows_xpath}, root, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const rows = [];
        for (let i = 0; i < rowNodes.snapshotLength; i++) {{
            const tr = rowNodes.snapshotItem(i);
            if (!tr.classList.contains('table-row-interactive')) continue;
            const cells = [];
            for (const td of tr.querySelectorAll(':scope > td')) {{
                // The outer ``v-align-middle`` span carries the display value (with its sign for
                // amounts); description cells have no such span, so fall back to the first span.
                const value = td.querySelector('span.v-align-middle') || td.querySelector('span');
                cells.push(text(value || td));
            }}
            rows.push(cells);
        }}
        return {{headers, rows}};
        """
        return cast("dict[str, Any] | None", self.driver.execute_script(script))

    def _parse_posted_transactions_from_page(self) -> list[tuple[datetime, str, float]]:
        table = self._extract_table_from_page()
        if not table:
            return []
        headers: list[str] = table.get("headers") or []
        raw_rows: list[list[str]] = table.get("rows") or []

        # Column layout differs by account type (credit card: Transaction date | Description |
        # Money in/out; bank: Date | Description | Money out | Money in | Balance), so map columns
        # from the header row rather than hardcoding indices.
        date_idx = next((i for i, h in enumerate(headers) if "date" in h.lower()), 0)
        desc_idx = next((i for i, h in enumerate(headers) if h.strip().lower() == "description"), 1)
        amount_idxs = [i for i, h in enumerate(headers) if h.strip() in _AMOUNT_HEADERS] or [2]

        transactions = []
        for cells in raw_rows:
            if date_idx >= len(cells):
                continue
            date_text = cells[date_idx]
            if not date_text:
                continue
            desc = cells[desc_idx] if desc_idx < len(cells) else ""
            amount_text = next((cells[i] for i in amount_idxs if i < len(cells) and cells[i].strip()), "")
            try:
                txn_date = datetime.strptime(date_text.strip(), "%b %d, %Y")
            except ValueError:
                logger.warning("Could not parse date: %s — skipping row", date_text)
                continue
            amount = self._parse_amount_from_web(amount_text, date_text=date_text, description=desc)
            transactions.append((txn_date, desc, amount))

        return transactions

    # ── pagination helpers ─────────────────────────────────────────────

    def _read_range_label(self) -> tuple[int, int, int] | None:
        """Parse the "1-20 of 126" pagination label into ``(start, end, total)`` ints."""
        elements = self.driver.find_elements(By.CSS_SELECTOR, ConnectionElementId.PAGINATION_RANGE_LABEL)
        if not elements:
            return None
        match = _RANGE_LABEL_RE.search(elements[0].text)
        if not match:
            return None
        return cast(
            "tuple[int, int, int]",
            tuple(int(group.replace(",", "")) for group in match.groups()),
        )

    @staticmethod
    def _format_range(page_range: tuple[int, int, int] | None) -> str:
        if page_range is None:
            return "range unknown"
        start, end, total = page_range
        return f"{start}-{end} of {total}"

    def _set_max_rows_per_page(self) -> None:
        """Best-effort: switch the page-size selector to its largest option so fewer pages are
        walked (e.g. 126 rows becomes 2 pages at 100 instead of 7 at 20). Never fatal."""
        select = json.dumps(str(ConnectionElementId.ROWS_PER_PAGE_SELECT))
        script = f"""
        const sel = document.querySelector({select});
        if (!sel) return null;
        const values = Array.from(sel.options).map(o => parseInt(o.value, 10)).filter(n => !isNaN(n));
        if (!values.length) return null;
        const max = Math.max(...values);
        if (String(sel.value) === String(max)) return max;
        sel.value = String(max);
        sel.dispatchEvent(new Event('change', {{bubbles: true}}));
        return max;
        """
        before = self._read_range_label()
        try:
            chosen = self.driver.execute_script(script)
        except Exception as e:  # pragma: no cover - defensive; the parse still works at 20/page
            logger.warning("Could not set BMO rows-per-page: %s", e)
            return
        if not chosen:
            return
        logger.info("Set BMO rows-per-page to %s", chosen)
        # Give the table time to reflow to the larger window before parsing page 1.
        try:
            self._wait_until(
                "apply BMO rows-per-page change",
                lambda _driver: True if self._read_range_label() not in (None, before) else None,
                DelaySeconds.PAGINATION_WAIT,
                timeout_log_level=logging.INFO,
            )
        except TimeoutException:
            logger.info("BMO rows-per-page reflow not observed; proceeding at current page size")

    def _wait_for_page_advance(self, previous_range: tuple[int, int, int] | None) -> bool:
        """Wait until the pager moves to a new range (and rows are present) after a Next click."""

        def advanced(driver) -> bool | None:
            current = self._read_range_label()
            if current is None:
                return None
            if previous_range is not None and current == previous_range:
                return None
            rows = driver.find_elements(By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_ROW_INTERACTIVE)
            return True if rows else None

        try:
            self._wait_until(
                "wait for BMO transaction page to advance after pagination",
                advanced,
                DelaySeconds.PAGINATION_WAIT,
                screenshot_name="bmo_pagination_stall",
                timeout_log_level=logging.INFO,
            )
            return True
        except TimeoutException:
            return False

    def _login(self, username: SecretString, password: SecretString) -> None:
        is_cookie_banner_already_dismissed = self._dismiss_cookie_banner()
        logger.info("Filling login credentials")
        username_field = self._find_element("enter BMO username", By.XPATH, ConnectionElementId.USERNAME)
        if not is_cookie_banner_already_dismissed:
            self._dismiss_cookie_banner()
        self._send_keys_verified("enter BMO username", username_field, username.reveal(), sensitive=True)
        password_field = self._find_element("enter BMO password", By.XPATH, ConnectionElementId.PASSWORD)
        self._send_keys_verified("enter BMO password", password_field, password.reveal(), sensitive=True)
        self._ensure_password_populated(password)
        sign_in_btn = self._find_sign_in_button()
        self._click("click BMO sign-in button", sign_in_btn)

    def _find_sign_in_button(self) -> ElementHandle:
        """Locate the login form's submit button, trying each known markup in turn.

        BMO re-renders the button with a random UUID ``id`` on every load and has changed its
        attributes between builds, so a single locator goes stale silently. Fallbacks run
        most- to least-specific; the last one is allowed to raise so the failure still names a
        locator."""
        *fallbacks, last = _SIGN_IN_LOCATORS
        for locator in fallbacks:
            try:
                return self._find_element("click BMO sign-in button", By.XPATH, locator)
            except ScraperError:
                logger.info("BMO sign-in button not found via %s; trying the next locator", locator)
        return self._find_element("click BMO sign-in button", By.XPATH, last)

    def _ensure_password_populated(self, password: SecretString) -> None:
        logger.info("Verifying BMO password field remains populated after cookie-banner handling")
        try:
            password_field = self._find_element(
                "verify BMO password before sign-in",
                By.XPATH,
                ConnectionElementId.PASSWORD,
            )
            password_value = password_field.get_attribute("value")
        except WebDriverException as e:
            self._save_screenshot("bmo_password_verification_failed")
            raise WebDriverException("Failed while verifying BMO password before sign-in") from e

        if password_value == password.reveal():
            logger.info("BMO password field is populated before sign-in")
            return

        logger.warning("BMO password field was cleared before sign-in; re-entering the redacted password")
        self._send_keys_verified(
            "re-enter BMO password before sign-in",
            password_field,
            password.reveal(),
            sensitive=True,
            screenshot_name="bmo_password_reentry_empty",
        )
        logger.info("BMO password field is populated after re-entry")

    def _dismiss_cookie_banner(self) -> bool:
        try:
            accept_btn = self._wait_until(
                "find clickable BMO cookie accept button",
                clickable(By.XPATH, ConnectionElementId.COOKIE_ACCEPT),
                DelaySeconds.COOKIE_BANNER_TIMEOUT,
                timeout_log_level=logging.INFO,
            )
            self._click("dismiss BMO cookie consent banner", accept_btn)
            self._wait_until(
                "wait for BMO cookie consent banner to disappear",
                invisible(By.ID, "onetrust-banner-sdk"),
                DelaySeconds.COOKIE_BANNER_TIMEOUT,
                timeout_log_level=logging.INFO,
            )
            logger.info("Cookie consent banner dismissed")
            return True
        except TimeoutException:
            logger.info("No cookie consent banner found, proceeding")
            return False

    def _wait_for_2fa_or_success(self) -> Literal["2fa", "success"]:
        """Wait for either a 2FA screen or the success URL after credentials are submitted.

        Which 2FA screen counts is deliberately broad: the delivery-method step current builds
        open on, the enter-code step BMO skips ahead to when a code is already in flight, and the
        NEXT interstitial older builds showed. Keying on one of them alone is what made the
        previous rebuild read as "login timed out or credentials are invalid".

        Logs the BMO login error banner text as soon as it appears, while continuing to wait
        for the normal outcome (or the timeout) so behavior is otherwise unchanged.
        """
        logged_errors: set[str] = set()

        def reached_outcome(driver) -> bool:
            if driver.current_url == BMO_SUCCESS_URL:
                return True
            if any(driver.find_elements(By.XPATH, locator) for locator in _MFA_SCREEN_LOCATORS):
                return True
            banner_text = self._extract_login_error()
            if banner_text and banner_text not in logged_errors:
                logged_errors.add(banner_text)
                logger.error("BMO login error banner displayed: %s", banner_text)
            return False

        try:
            self._wait_until(
                "reach BMO success URL or detect BMO 2FA screen after login",
                reached_outcome,
                DelaySeconds.LOGIN_SUCCESS_TIMEOUT,
            )
        except TimeoutException as e:
            self._save_screenshot("bmo_login_timeout")
            error_message = self._extract_login_error()
            if error_message:
                raise TimeoutException(f"BMO login failed: {error_message}") from e
            raise TimeoutException("BMO login timed out or credentials are invalid") from e

        if self.driver.current_url == BMO_SUCCESS_URL:
            return "success"
        return "2fa"

    # ── 2FA: element lookup ────────────────────────────────────────────

    def _first_displayed(self, locators: tuple[str, ...]) -> tuple[str, ElementHandle] | None:
        """First enabled, on-screen element matching any of ``locators``, with the one that found it.

        Locators are tried in order, so an earlier (more specific) one always wins over a later
        fallback. Elements BMO keeps mounted but hidden — the previous OTP step, OneTrust's
        preference centre — are skipped rather than clicked into the void."""
        for locator in locators:
            for element in self.driver.find_elements(By.XPATH, locator):
                if element.get_attribute("disabled") is not None:
                    continue
                if element.is_displayed():
                    return locator, element
        return None

    def _wait_for_any(
        self,
        description: str,
        locators: tuple[str, ...],
        timeout: int,
        *,
        screenshot_name: str | None = None,
    ) -> tuple[str, ElementHandle]:
        return self._wait_until(
            description,
            lambda _driver: self._first_displayed(locators),
            timeout,
            screenshot_name=screenshot_name,
        )

    def _click_first_match(
        self,
        description: str,
        locators: tuple[str, ...],
        timeout: int,
        *,
        human: bool = True,
    ) -> None:
        """Click whichever of ``locators`` matches first, re-locating once if the click is lost.

        Angular re-renders these screens as its form state changes, which can retire the handle
        between finding it and clicking it; one re-locate turns that race into a retry instead of
        a failed login."""
        locator, element = self._wait_for_any(f"find {description}", locators, timeout)
        logger.info("Found %s via %s", description, _locator_name(locator))
        try:
            self._click(f"click {description}", element, human=human)
        except (ScraperError, StaleElementReferenceException) as e:
            logger.info("%s went stale before the click (%s); re-locating it", description, e)
            _, element = self._wait_for_any(f"re-find {description}", locators, timeout)
            self._click(f"click {description}", element, human=human)

    def _click_until(
        self,
        description: str,
        locators: tuple[str, ...],
        took_effect: Callable[[], bool],
    ) -> bool:
        """Click candidates until the page itself confirms the click took. Reports whether it did.

        A form control on these screens is only really clicked once the DOM says so, so every
        candidate is checked against ``took_effect`` afterwards: a locator that matched the wrong
        row, or a click a banner swallowed, moves on to the next candidate instead of being taken
        on faith. Real mouse clicks are tried across the whole chain first; only if none of them
        registers does a second pass fall back to DOM clicks, which reach a control that something
        invisible is sitting on top of."""
        for use_dom_click in (False, True):
            for locator in locators:
                match = self._first_displayed((locator,))
                if match is None:
                    continue
                how = _locator_name(locator)
                if use_dom_click:
                    self._click_via_dom(f"{description} (via {how})", match[1])
                else:
                    self._click(f"{description} (via {how})", match[1], human=True)
                if self._confirmed_within(took_effect):
                    logger.info("Completed: %s (via %s%s)", description, how, ", DOM click" if use_dom_click else "")
                    return True
                logger.info("Nothing changed after %s via %s; trying the next candidate", description, how)
        return False

    @staticmethod
    def _confirmed_within(took_effect: Callable[[], bool], seconds: float = _CLICK_CONFIRM_SECONDS) -> bool:
        """Give Angular a moment to apply a click before calling it lost.

        Without the grace period a slow re-render reads as a click that missed, and clicking again
        would toggle a checkbox straight back off."""
        deadline = monotonic() + seconds
        while True:
            if took_effect():
                return True
            if monotonic() >= deadline:
                return False
            sleep(0.2)

    def _checkbox_state(self, css_selector: str) -> bool | None:
        """Whether the first checkbox matching ``css_selector`` is ticked; ``None`` if there is none.

        Read as the live DOM property rather than the HTML attribute: BMO's design system never
        writes ``checked`` back into the markup, so the attribute stays absent however the box
        is left."""
        result = self.driver.execute_script(
            f"const box = document.querySelector({json.dumps(css_selector)}); return box ? !!box.checked : null;"
        )
        return None if result is None else bool(result)

    # ── 2FA: asking BMO to text the code ───────────────────────────────

    def _handle_2fa_initiation(self) -> None:
        """Ask BMO to send the OTP code to the user's phone.

        Three steps on one screen — choose the phone delivery option, confirm the code won't be
        shared, then SEND CODE — each located through its own fallback chain and each verified
        before the next one runs, so a reworded label or a re-rendered form shows up as a named
        failure rather than as a code that never arrives."""
        self._dismiss_cookie_banner_if_showing()
        self._click_legacy_next_step_if_present()
        self._wait_until(
            "reach the BMO 2FA delivery-method step",
            present(By.XPATH, ConnectionElementId.MFA_METHOD_STEP),
            DelaySeconds.MFA_STEP_TIMEOUT,
            screenshot_name="bmo_2fa_method_step_missing",
        )
        self._select_phone_otp_method()
        self._accept_otp_disclaimer()
        # Clicked once and only once, unlike the form controls above: a SEND CODE that did land
        # but was slow to advance the screen would be clicked again, and the user's phone would
        # buzz twice with two codes, only one of which BMO accepts. If it really was lost, the
        # wait below says so plainly instead.
        self._click_first_match("the BMO SEND CODE button", _MFA_SEND_CODE_LOCATORS, DelaySeconds.MFA_STEP_TIMEOUT)
        self._wait_until(
            "reach the BMO enter-OTP-code step after requesting a code",
            present(By.XPATH, ConnectionElementId.OTP_STEP),
            DelaySeconds.MFA_STEP_TIMEOUT,
            screenshot_name="bmo_2fa_code_step_missing",
        )

    def _dismiss_cookie_banner_if_showing(self) -> None:
        """Clear the consent banner off the 2FA screen before anything on it is clicked.

        The banner is normally dealt with on the login page, but it comes back on a fresh consent
        state and covers the middle of the OTP form — where it eats mouse clicks without leaving a
        trace. Probed first so a session that never sees one doesn't pay the banner's wait."""
        if self._first_displayed((ConnectionElementId.COOKIE_ACCEPT,)) is None:
            return
        logger.info("Cookie consent banner is covering BMO's 2FA screen; dismissing it first")
        self._dismiss_cookie_banner()

    def _click_legacy_next_step_if_present(self) -> None:
        """Older BMO builds put a NEXT interstitial ahead of the delivery-method list."""
        match = self._first_displayed((ConnectionElementId.MFA_NEXT_BUTTON,))
        if match is None:
            logger.info("No 2FA NEXT interstitial on this BMO build; going straight to the method step")
            return
        self._click("click the BMO 2FA NEXT button", match[1], human=True)

    def _select_phone_otp_method(self) -> None:
        """Pick the option that texts the code to the user's phone, and prove it took.

        BMO can offer several delivery methods, so the phone one is chosen by what the option
        says (its label, then its radio value) rather than by position, and the form is read back
        afterwards: a click that landed on the wrong option, or on nothing at all, moves on to
        the next locator instead of quietly requesting an email code."""
        selected = self._selected_otp_method()
        if selected is not None and _is_phone_delivery_method(selected):
            logger.info("BMO already has the phone delivery method selected (%s)", selected)
            return

        logger.info("Selecting the phone delivery method for the BMO verification code")

        def phone_is_selected() -> bool:
            current = self._selected_otp_method()
            return current is not None and _is_phone_delivery_method(current)

        if self._click_until("select the BMO phone delivery method", _MFA_PHONE_CHOICE_LOCATORS, phone_is_selected):
            logger.info("BMO will send the verification code to %s", self._selected_otp_method())
            return

        selected = self._selected_otp_method()
        raise ScraperError(
            "Could not select a phone delivery method on BMO's 2FA screen"
            + (f"; the form has {selected!r} selected instead" if selected else "")
        )

    def _selected_otp_method(self) -> str | None:
        """Label (or value) of the delivery option the OTP form currently has selected."""
        try:
            selected = self.driver.execute_script(_JS_SELECTED_OTP_METHOD)
        except WebDriverException:
            return None
        return str(selected) if selected else None

    def _accept_otp_disclaimer(self) -> None:
        """Tick the "I won't give this code to anyone" box BMO requires before it sends one.

        The box is optional only in the sense that a build without it should still be able to
        request a code; a box that is present and stays unticked is fatal, because SEND CODE
        silently does nothing while the form is invalid."""
        state = self._checkbox_state(ConnectionElementId.MFA_AGREE_CHECKBOX_INPUT)
        if state is None:
            logger.info("No confirmation checkbox on this BMO 2FA screen; nothing to tick")
            return
        if state:
            logger.info("BMO's 2FA confirmation checkbox is already ticked")
            return

        logger.info("Ticking BMO's 2FA confirmation checkbox")
        if self._click_until(
            "tick BMO's 2FA confirmation checkbox",
            _MFA_AGREE_CHECKBOX_LOCATORS,
            lambda: self._checkbox_state(ConnectionElementId.MFA_AGREE_CHECKBOX_INPUT) is True,
        ):
            return

        raise ScraperError("Could not tick the confirmation checkbox on BMO's 2FA screen")

    def _capture_cookies(self, account_uuid: str) -> None:
        account_url = self._resolve_account_url(account_uuid)
        logger.info("Navigating to account page to prime XSRF-TOKEN cookie")
        self._navigate("open BMO account page to prime XSRF token cookie", account_url)
        sleep(DelaySeconds.ACCOUNT_NAV_WAIT)

        self.cookies = self.driver.get_cookies()
        logger.info("Session cookies captured (%d cookies)", len(self.cookies))

    # ── account routing ────────────────────────────────────────────────

    def _resolve_account_url(self, account_uuid: str) -> str:
        """Resolve a UUID to its real /account-details/{ba,cc}/{uuid} URL.

        BMO routes bank accounts (``/ba/``) and credit cards (``/cc/``) to different paths, so the
        prefix cannot be assumed. The side nav on any account-details page lists every account with
        its correct href, so one loaded page maps them all; the result is cached, meaning a
        multi-account session probes at most once regardless of ordering.
        """
        if account_uuid in self._account_urls:
            return self._account_urls[account_uuid]

        for prefix in ("cc", "ba"):
            probe_url = f"{BMO_ACCOUNT_DETAILS_BASE}/{prefix}/{account_uuid}"
            self._navigate(f"probe BMO account page ({prefix}) to read the side nav", probe_url)
            try:
                self._wait_until(
                    "load BMO side navigation",
                    present(By.CSS_SELECTOR, ConnectionElementId.SIDE_NAV_LINK),
                    DelaySeconds.ACCOUNT_SHELL_TIMEOUT,
                    timeout_log_level=logging.INFO,
                )
            except TimeoutException:
                logger.info("BMO side nav did not load for %s probe; trying next prefix", prefix)
                continue
            self._read_side_nav_map()
            if account_uuid in self._account_urls:
                break

        if account_uuid not in self._account_urls:
            available = ", ".join(f"{self._account_names.get(uuid, '?')} ({uuid})" for uuid in self._account_urls)
            raise ValueError(
                f"BMO account {account_uuid} was not found on this profile. Accounts detected: {available or 'none'}."
            )
        return self._account_urls[account_uuid]

    def _read_side_nav_map(self) -> None:
        """Populate the UUID -> URL / UUID -> name maps from every side-nav link on the page."""
        link_selector = json.dumps(str(ConnectionElementId.SIDE_NAV_LINK))
        script = f"""
        const out = [];
        for (const a of document.querySelectorAll({link_selector})) {{
            const href = a.href || a.getAttribute('href');
            if (href) out.push([href, (a.textContent || '').trim()]);
        }}
        return out;
        """
        raw: list[list[str]] = self.driver.execute_script(script) or []
        for href, name in raw:
            match = _ACCOUNT_HREF_RE.search(href)
            if not match:
                continue
            uuid = match.group(1)
            self._account_urls[uuid] = href
            self._account_names[uuid] = name

    def _extract_login_error(self) -> str | None:
        try:
            banner = self.driver.find_element(By.XPATH, ConnectionElementId.LOGIN_ERROR_BANNER)
            text = banner.text.strip()
            return text if text else None
        except Exception:
            return None

    def _extract_otp_error(self) -> str | None:
        """Text of the inline error BMO shows against a rejected or malformed OTP code."""
        try:
            for element in self.driver.find_elements(By.XPATH, ConnectionElementId.OTP_ERROR):
                if not element.is_displayed():
                    continue
                text = element.text.strip()
                if text:
                    return text
        except Exception:
            return None
        return None

    @staticmethod
    def _parse_filename_from_header(header_value: str) -> str | None:
        match = re.search(r"filename=([^\s;]+)", header_value)
        return match.group(1) if match else None
