from __future__ import annotations

import csv
import logging
import shutil
import zipfile
from datetime import date
from http import HTTPStatus
from pathlib import Path
from time import sleep
from typing import Any, Final

import requests
from selenium.common import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pennyspy.scrapers.base import AuthStep, BankScraper
from pennyspy.scrapers.get_required_env_var import get_required_env_var
from pennyspy.scrapers.scotiabank.connection_element_id import ConnectionElementId
from pennyspy.scrapers.scotiabank.delay_seconds import DelaySeconds
from pennyspy.scrapers.scraper import BrowserConfig, create_browser

SCOTIA_HOME_URL: Final[str] = "https://www.scotiabank.com/ca/en/personal.html"
SCOTIA_SIGN_IN_LINK: Final[str] = "//a[contains(@class, 'btn-signin')]"
SCOTIA_SUCCESS_DOMAIN: Final[str] = "secure.scotiabank.com"
SCOTIA_SUMMARY_URL: Final[str] = "https://secure.scotiabank.com/api/accounts/summary"
SCOTIA_TRANSACTIONS_URL: Final[str] = "https://secure.scotiabank.com/api/transactions/transaction-history"

logger = logging.getLogger(__name__)


class ScotiaBank(BankScraper):
    def __init__(self, config: BrowserConfig = BrowserConfig()):
        super().__init__(config=config)
        self.cookies: list[dict] | None = None

    # ── BankScraper interface ──────────────────────────────────────────

    def start_auth(self, **kwargs: Any) -> AuthStep:
        # Temporary: allow overriding headless mode for debugging
        headless = kwargs.get("headless", True)
        if not headless and self._config.headless:
            logger.info("Rebuilding browser in non-headless mode for debugging")
            self.driver.quit()
            shutil.rmtree(self._user_data_dir, ignore_errors=True)
            config = BrowserConfig(headless=False)
            self.driver, self._user_data_dir = create_browser(config)

        try:
            return self._do_start_auth()
        except Exception:
            logger.exception("start_auth failed — saving screenshot")
            self._save_screenshot("scotia_start_auth_error")
            raise

    def _do_start_auth(self) -> AuthStep:
        logger.info("Navigating to Scotiabank home page: %s", SCOTIA_HOME_URL)
        self.driver.get(SCOTIA_HOME_URL)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)
        logger.info("Home page loaded — current URL: %s", self.driver.current_url)

        self._dismiss_cookie_banner()

        logger.info("Clicking Sign In button")
        sign_in_link = WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
            EC.element_to_be_clickable((By.XPATH, SCOTIA_SIGN_IN_LINK))
        )
        sign_in_link.click()

        logger.info("Waiting for login form to load")
        WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
            EC.presence_of_element_located((By.XPATH, ConnectionElementId.USERNAME))
        )
        logger.info("Login page loaded — current URL: %s", self.driver.current_url)
        logger.info("Page title: %s", self.driver.title)

        username = get_required_env_var("PENNYSPY_SCOTIAU")
        password = get_required_env_var("PENNYSPY_SCOTIAP")
        self._login(username, password)
        self._check_for_wrong_login()

        logger.info("Credentials submitted — current URL: %s", self.driver.current_url)

        outcome = self._wait_for_2sv_or_success()
        if outcome == "success":
            logger.info("Logged in — 2SV was approved during login")
            self._capture_cookies()
            return AuthStep(status="authenticated")

        # 2sv-confirmation page means user already approved on phone —
        # just need to click continue (and optionally trust this device)
        logger.info("2SV confirmed — handling trust device and continuing")
        self._handle_trust_device()
        self._capture_cookies()
        return AuthStep(status="authenticated")

    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        logger.info("continue_auth called — already authenticated")
        return AuthStep(status="authenticated")

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        from_date: date = kwargs["from_date"]
        to_date: date = kwargs["to_date"]

        accounts = self._get_accounts()
        downloaded: list[Path] = []

        for account in accounts:
            account_key = account["key"]
            display_id = account.get("displayId", "unknown")
            account_type = self._resolve_account_type(account)
            description = account.get("description", "")
            logger.info("Processing account %s (%s, type=%s)", display_id, description, account_type)
            try:
                file_path = self._download_transactions_for_account(
                    account_key=account_key,
                    account_type=account_type,
                    display_id=display_id,
                    from_date=from_date,
                    to_date=to_date,
                    export_directory=export_directory,
                )
                downloaded.append(file_path)
            except ValueError as e:
                logger.warning("No transactions for account %s: %s", display_id, e)

        if not downloaded:
            raise ValueError(f"No transactions found for any account between {from_date} and {to_date}")

        if len(downloaded) == 1:
            return downloaded[0]

        # Multiple files — zip them together
        today_str = date.today().isoformat()
        zip_path = export_directory / f"scotiabank_transactions_{today_str}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in downloaded:
                zf.write(f, f.name)
        logger.info("Zipped %d file(s) into %s", len(downloaded), zip_path)
        return zip_path

    # ── Internal implementation ────────────────────────────────────────

    def _login(self, username: str, password: str) -> None:
        logger.info("Looking for username field")
        username_field = self.driver.find_element(By.XPATH, ConnectionElementId.USERNAME)
        logger.info("Username field found — filling credentials")
        username_field.send_keys(username)

        password_field = self.driver.find_element(By.XPATH, ConnectionElementId.PASSWORD)
        logger.info("Password field found — filling password")
        password_field.send_keys(password)

        sign_in_btn = self.driver.find_element(By.XPATH, ConnectionElementId.SIGN_IN)
        logger.info("Sign-in button found — clicking")
        sign_in_btn.click()
        logger.info("Sign-in button clicked")

    def _check_for_wrong_login(self) -> None:
        logger.info("Checking for login error banner (waiting up to %ds)", DelaySeconds.PAGE_LOADING)
        try:
            WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
                EC.visibility_of_element_located((By.XPATH, ConnectionElementId.LOGIN_ERROR))
            )
            self._save_screenshot("scotia_wrong_login")
            raise ValueError("Username and password seem to be invalid, failed to connect.")
        except TimeoutException:
            logger.info("No error banner detected — credentials appear valid")
            logger.info("Current URL after credential check: %s", self.driver.current_url)

    def _wait_for_2sv_or_success(self) -> str:
        """Wait for either 2SV prompt or direct redirect to success domain.

        Returns "2sv" if the 2SV confirmation page appeared, "success" if
        already on the authenticated domain (user approved quickly or device
        was already trusted).
        """
        logger.info("Waiting for 2SV or direct success (timeout: %ds)...", DelaySeconds.LOGIN_SUCCESS_TIMEOUT)
        try:
            WebDriverWait(self.driver, DelaySeconds.LOGIN_SUCCESS_TIMEOUT).until(
                lambda d: SCOTIA_SUCCESS_DOMAIN in d.current_url or "2sv-confirmation" in d.current_url,
            )
        except TimeoutException as e:
            self._save_screenshot("scotia_login_timeout")
            raise TimeoutException("Scotiabank login timed out after credentials") from e

        current = self.driver.current_url
        if SCOTIA_SUCCESS_DOMAIN in current and "2sv" not in current:
            logger.info("Direct success — URL: %s", current)
            return "success"

        # We're on the 2SV page — user still needs to approve on their phone
        logger.info("2SV waiting page detected — URL: %s", current)
        return "2sv"

    def _handle_trust_device(self) -> None:
        try:
            checkbox = WebDriverWait(self.driver, DelaySeconds.COOKIE_INIT).until(
                EC.element_to_be_clickable((By.XPATH, ConnectionElementId.TRUST_DEVICE_CHECKBOX))
            )
            logger.info("Checking 'trust this device' checkbox")
            checkbox.click()
        except TimeoutException:
            logger.info("No trust device checkbox found — skipping")

        logger.info("Clicking continue button")
        continue_btn = WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.TRUST_DEVICE_CONTINUE))
        )
        continue_btn.click()

        logger.info(
            "Waiting for redirect to %s (timeout: %ds)", SCOTIA_SUCCESS_DOMAIN, DelaySeconds.LOGIN_SUCCESS_TIMEOUT
        )
        WebDriverWait(self.driver, DelaySeconds.LOGIN_SUCCESS_TIMEOUT).until(
            EC.url_contains(SCOTIA_SUCCESS_DOMAIN),
        )
        logger.info("Connected to Scotiabank — final URL: %s", self.driver.current_url)

    def _dismiss_cookie_banner(self) -> None:
        logger.info("Checking for cookie consent banner")
        try:
            accept_btn = WebDriverWait(self.driver, DelaySeconds.COOKIE_INIT).until(
                EC.element_to_be_clickable((By.XPATH, ConnectionElementId.COOKIE_ACCEPT))
            )
            accept_btn.click()
            logger.info("Cookie consent banner dismissed")
        except TimeoutException:
            logger.info("No cookie consent banner found — continuing")

    def _get_accounts(self) -> list[dict[str, Any]]:
        """Fetch the account summary and return the list of products/accounts."""
        session = self._build_session()
        logger.info("Fetching account summary from %s", SCOTIA_SUMMARY_URL)
        resp = session.get(SCOTIA_SUMMARY_URL)
        assert resp.status_code == HTTPStatus.OK, (
            f"Failed to fetch account summary: {resp.status_code} {resp.text[:200]}"
        )
        products: list[dict[str, Any]] = resp.json()["data"]["products"]
        logger.info("Found %d account(s)", len(products))
        return products

    _PRODUCT_CATEGORY_MAP: dict[str, str] = {
        "CREDITCARDS": "CREDIT",
        "DAYTODAY": "DAYTODAY",
        "BORROWING": "CREDIT",
    }

    @classmethod
    def _resolve_account_type(cls, account: dict[str, Any]) -> str:
        """Map account productCategory to the accountType query param for the transactions API."""
        category = (account.get("productCategory") or "").upper()
        return cls._PRODUCT_CATEGORY_MAP.get(category, "DAYTODAY")

    def _build_session(self) -> requests.Session:
        assert self.cookies is not None, "Cookies have not been captured yet."
        session = requests.Session()
        for cookie in self.cookies:
            session.cookies.set(cookie["name"], cookie["value"])
        return session

    def _download_transactions_for_account(
        self,
        account_key: str,
        account_type: str,
        display_id: str,
        from_date: date,
        to_date: date,
        export_directory: Path,
    ) -> Path:
        export_directory.mkdir(parents=True, exist_ok=True)
        session = self._build_session()

        url = f"{SCOTIA_TRANSACTIONS_URL}/{account_key}"
        params: dict[str, str] = {
            "accountType": account_type,
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        }

        transactions: list[dict[str, Any]] = []
        page = 1
        while True:
            logger.info("Fetching transactions for account %s page %d", display_id, page)
            resp = session.get(url, params=params)
            assert resp.status_code == HTTPStatus.OK, (
                f"Failed to fetch transactions: {resp.status_code} {resp.text[:200]}"
            )

            payload = resp.json()
            transactions.extend(self._extract_transactions(payload))

            cursor = payload.get("nextCursorKey")
            if not cursor:
                break
            params["cursor"] = cursor
            page += 1

        if not transactions:
            raise ValueError(f"No transactions found for account {display_id} between {from_date} and {to_date}")

        logger.info("Found %d transaction(s) for account %s across %d page(s)", len(transactions), display_id, page)

        headers = [
            "Date", "PostedDate", "TransactionType", "Description", "Details",
            "Category", "MerchantName", "MerchantCity", "MerchantState",
            "MerchantCountry", "MerchantCategoryCode", "Amount", "Currency",
            "Balance", "Status", "PurchaseType", "TransactionId",
        ]
        rows: list[list[str]] = []
        for txn in transactions:
            merchant = txn.get("merchant") or {}
            amount_obj = txn.get("transactionAmount") or {}
            balance_obj = txn.get("balance") or txn.get("runningBalance") or {}
            rows.append([
                txn.get("transactionDate", ""),
                txn.get("postedDate", txn.get("dateInserted", "")),
                txn.get("transactionType", ""),
                txn.get("description", ""),
                (txn.get("subdescription") or "").strip(),
                (txn.get("transactionCategory") or txn.get("type") or "").strip(),
                (merchant.get("name") or "").strip(),
                (merchant.get("city") or "").strip(),
                (merchant.get("state") or "").strip(),
                (merchant.get("countryCode") or "").strip(),
                merchant.get("categoryCode") or "",
                str(amount_obj.get("amount", "")),
                amount_obj.get("currencyCode", "CAD"),
                str(balance_obj.get("amount", "")),
                txn.get("status") or "",
                txn.get("purchaseType") or "",
                txn.get("transactionId") or "",
            ])

        today_str = date.today().isoformat()
        filename = f"scotiabank_{display_id}_{today_str}.csv"
        file_path = export_directory / filename

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

        logger.info("Transactions saved: %s (%d rows)", file_path, len(rows))
        return file_path

    @staticmethod
    def _extract_transactions(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Handle both day-to-day (flat array) and credit (pending/settled) response shapes."""
        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        # Credit accounts: data has "pending" and "settled" keys
        transactions: list[dict[str, Any]] = []
        transactions.extend(data.get("settled", []))
        transactions.extend(data.get("pending", []))
        return transactions

    def _capture_cookies(self) -> None:
        logger.info("Navigating to %s to prime session cookies", SCOTIA_SUMMARY_URL)
        self.driver.get(SCOTIA_SUMMARY_URL)
        sleep(DelaySeconds.COOKIE_INIT)
        self.cookies = self.driver.get_cookies()
        logger.info("Session cookies captured (%d cookies)", len(self.cookies))
