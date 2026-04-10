from __future__ import annotations

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
SCOTIA_SUMMARY_URL: Final[str] = "https://secure.scotiabank.com/my-accounts/api/summary"

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
            self.save_screenshot("scotia_start_auth_error")
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

        return AuthStep(
            status="waiting_for_external",
            message="Approve the sign-in request in your Scotiabank mobile app.",
        )

    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        logger.info("continue_auth called — current URL: %s", self.driver.current_url)
        if self.cookies is not None:
            logger.info("Already authenticated — skipping 2SV wait")
            return AuthStep(status="authenticated")

        self._wait_for_2sv_completion()
        self._capture_cookies()
        return AuthStep(status="authenticated")

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        from_date: date = kwargs["from_date"]
        to_date: date = kwargs["to_date"]

        accounts = self._get_accounts()
        downloaded: list[Path] = []

        for account in accounts:
            account_key = account["key"]
            display_id = account.get("displayId", "unknown")
            description = account.get("description", "")
            logger.info("Processing account %s (%s)", display_id, description)
            try:
                file_path = self._download_statement_for_account(
                    account_key=account_key,
                    display_id=display_id,
                    from_date=from_date,
                    to_date=to_date,
                    export_directory=export_directory,
                )
                downloaded.append(file_path)
            except ValueError as e:
                logger.warning("No statements for account %s: %s", display_id, e)

        if not downloaded:
            raise ValueError(f"No statements found for any account between {from_date} and {to_date}")

        if len(downloaded) == 1:
            return downloaded[0]

        # Multiple files — zip them together
        today_str = date.today().isoformat()
        zip_path = export_directory / f"scotiabank_statements_{today_str}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in downloaded:
                zf.write(f, f.name)
        logger.info("Zipped %d statement(s) into %s", len(downloaded), zip_path)
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
            self.save_screenshot("scotia_wrong_login")
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
                lambda d: (
                    SCOTIA_SUCCESS_DOMAIN in d.current_url
                    or "2sv-confirmation" in d.current_url
                ),
            )
        except TimeoutException as e:
            self.save_screenshot("scotia_login_timeout")
            raise TimeoutException("Scotiabank login timed out after credentials") from e

        current = self.driver.current_url
        if SCOTIA_SUCCESS_DOMAIN in current and "2sv" not in current:
            logger.info("Direct success — URL: %s", current)
            return "success"

        # We're on the 2SV page — user still needs to approve on their phone
        logger.info("2SV waiting page detected — URL: %s", current)
        return "2sv"

    def _wait_for_2sv_completion(self) -> None:
        """Wait for user to approve 2SV, then handle trust device and continue."""
        logger.info("Waiting for 2SV approval on Scotiabank mobile app (timeout: %ds)...", DelaySeconds.TWO_FACTOR_TIMEOUT)

        # Wait for the trust device checkbox to appear (signals phone approval)
        try:
            WebDriverWait(self.driver, DelaySeconds.TWO_FACTOR_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, ConnectionElementId.TRUST_DEVICE_CHECKBOX)),
            )
        except TimeoutException as e:
            # Maybe already redirected past 2SV
            if SCOTIA_SUCCESS_DOMAIN in self.driver.current_url and "2sv" not in self.driver.current_url:
                logger.info("Already on success page — URL: %s", self.driver.current_url)
                return
            logger.error("2SV timed out — current URL: %s", self.driver.current_url)
            self.save_screenshot("scotia_2sv_timeout")
            raise TimeoutException("Scotiabank 2SV timed out — user did not approve in time") from e

        logger.info("2SV approved — handling trust device")
        self._handle_trust_device()

    def _handle_trust_device(self) -> None:
        logger.info("Looking for trust device checkbox")
        checkbox = WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.TRUST_DEVICE_CHECKBOX))
        )
        logger.info("Checking 'trust this device' checkbox")
        checkbox.click()

        logger.info("Clicking continue button")
        self.driver.find_element(By.XPATH, ConnectionElementId.TRUST_DEVICE_CONTINUE).click()

        logger.info("Waiting for redirect to %s (timeout: %ds)", SCOTIA_SUCCESS_DOMAIN, DelaySeconds.LOGIN_SUCCESS_TIMEOUT)
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
        products = resp.json()["data"]["accounts"]["products"]
        logger.info("Found %d account(s)", len(products))
        return products

    def _build_session(self) -> requests.Session:
        assert self.cookies is not None, "Cookies have not been captured yet."
        session = requests.Session()
        for cookie in self.cookies:
            session.cookies.set(cookie["name"], cookie["value"])
        return session

    def _download_statement_for_account(
        self,
        account_key: str,
        display_id: str,
        from_date: date,
        to_date: date,
        export_directory: Path,
    ) -> Path:
        export_directory.mkdir(parents=True, exist_ok=True)
        session = self._build_session()

        # Step 1: List statements for the account in the date range
        statements_url = "https://secure.scotiabank.com/api/statements"
        params = {
            "accountKey": account_key,
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        }
        logger.info("Fetching statement list for account %s", display_id)
        resp = session.get(statements_url, params=params)
        assert resp.status_code == HTTPStatus.OK, (
            f"Failed to list statements: {resp.status_code} {resp.text[:200]}"
        )

        statements = resp.json().get("data", [])
        if not statements:
            raise ValueError(
                f"No statements found for account {display_id} "
                f"between {from_date} and {to_date}"
            )

        # Use the most recent statement
        statement_key = statements[0]["key"]
        logger.info(
            "Found %d statement(s) for account %s — downloading dated %s",
            len(statements),
            display_id,
            statements[0].get("statementDate", "unknown"),
        )

        # Step 2: Download the statement CSV
        download_url = f"https://secure.scotiabank.com/api/statements/{statement_key}"
        logger.info("Downloading statement CSV for account %s", display_id)
        dl_resp = session.get(download_url)
        assert dl_resp.status_code == HTTPStatus.OK, (
            f"Failed to download statement: {dl_resp.status_code} {dl_resp.text[:200]}"
        )

        today_str = date.today().isoformat()
        filename = f"scotiabank_{display_id}_{today_str}.csv"
        file_path = export_directory / filename
        file_path.write_bytes(dl_resp.content)
        logger.info("Statement saved: %s (%d bytes)", file_path, len(dl_resp.content))
        return file_path

    def _capture_cookies(self) -> None:
        logger.info("Navigating to %s to prime session cookies", SCOTIA_SUMMARY_URL)
        self.driver.get(SCOTIA_SUMMARY_URL)
        sleep(DelaySeconds.COOKIE_INIT)
        self.cookies = self.driver.get_cookies()
        logger.info("Session cookies captured (%d cookies)", len(self.cookies))
