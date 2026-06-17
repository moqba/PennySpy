from __future__ import annotations

import csv
import logging
import re
import secrets
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from time import sleep
from typing import Any, Final, Literal

import requests
from selenium.common import TimeoutException
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pennyspy.scrapers.base import AuthStep, BankScraper
from pennyspy.scrapers.bmo_bank.connection_element_id import ConnectionElementId
from pennyspy.scrapers.bmo_bank.delay_seconds import DelaySeconds
from pennyspy.scrapers.bmo_bank.get_default_filename import get_default_filename
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate
from pennyspy.scrapers.get_required_env_var import SecretString, get_required_env_var
from pennyspy.scrapers.scraper import BrowserConfig

BMO_LOGIN_URL: Final[str] = "https://www1.bmo.com/banking/digital/login"
BMO_SUCCESS_URL: Final[str] = "https://www1.bmo.com/banking/digital/accounts"
BMO_DOWNLOAD_URL: Final[str] = "https://www1.bmo.com/banking/services/accountdetails/downloadCCTransactions"

logger = logging.getLogger(__name__)


class BMOBank(BankScraper):
    def __init__(self, config: BrowserConfig = BrowserConfig()):
        super().__init__(config=config)
        self.cookies: list[dict] | None = None
        self._account_uuid: str | None = None
        self._user_agent: str = self.driver.execute_script("return navigator.userAgent")
        self._authenticated: bool = False

    # ── BankScraper interface ──────────────────────────────────────────

    def start_auth(self, **kwargs: Any) -> AuthStep:
        account_uuid: str = kwargs["account_uuid"]
        self._account_uuid = account_uuid
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

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        app_type: AppType = kwargs["app_type"]
        if kwargs.get("from_date") is not None:
            # Web scraping path — uses the live browser, no cookies needed
            return self._parse_transactions_from_web(
                from_date=kwargs["from_date"],
                export_directory=export_directory,
            )
        # API path — capture cookies if not already done (no-2FA path captures early)
        if self.cookies is None:
            self._capture_cookies()
        statement_date: StatementDate = kwargs["statement_date"]
        return self._download_transactions_via_api(
            app_type=app_type,
            statement_date=statement_date,
            export_directory=export_directory,
        )

    # ── Internal implementation ────────────────────────────────────────

    def _complete_2fa(self, otp_code: str) -> None:
        """Complete the 2FA UI flow. Does NOT capture cookies or quit the driver."""
        logger.info("Entering OTP code")
        try:
            otp_field = self._wait_until(
                "find visible BMO OTP input field",
                EC.visibility_of_element_located((By.XPATH, ConnectionElementId.OTP_INPUT)),
                DelaySeconds.MFA_STEP_TIMEOUT,
            )
        except TimeoutException as e:
            raise TimeoutException("Couldn't find OTP input field while completing BMO 2FA") from e
        self._send_keys("enter BMO OTP code", otp_field, otp_code, sensitive=True)

        try:
            confirm_btn = self._wait_until(
                "find clickable BMO OTP confirm button",
                EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_CONFIRM)),
                DelaySeconds.MFA_STEP_TIMEOUT,
            )
        except TimeoutException as e:
            raise TimeoutException("OTP confirm button is not available/clickable while completing BMO 2FA") from e
        self._click("click BMO OTP confirm button", confirm_btn)

        logger.info("Waiting for CONTINUE button")
        try:
            continue_btn = self._wait_until(
                "find clickable BMO continue button after OTP",
                EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_CONTINUE)),
                DelaySeconds.TWO_FACTOR_TIMEOUT,
            )
        except TimeoutException as e:
            raise TimeoutException("Continue button after OTP is not available/clickable while completing BMO 2FA") from e
        self._click("click BMO continue button after OTP", continue_btn)

        logger.info("Waiting for post-2FA redirect to %s", BMO_SUCCESS_URL)
        self._wait_until(
            f"complete BMO post-2FA redirect to {BMO_SUCCESS_URL}",
            EC.url_to_be(BMO_SUCCESS_URL),
            DelaySeconds.LOGIN_SUCCESS_TIMEOUT,
            screenshot_name="bmo_post_2fa_redirect_timeout",
        )

    def _download_transactions_via_api(
        self,
        app_type: AppType,
        statement_date: StatementDate,
        export_directory: Path | str,
    ) -> Path:
        assert self.cookies is not None, "Cookies have not been captured yet."
        assert self._account_uuid is not None

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
            "X-App-Current-Path": f"/banking/digital/account-details/cc/{self._account_uuid}",
            "X-Request-ID": request_id,
            "X-Original-Request-Time": now_time.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Referer": (
                f"https://www1.bmo.com/banking/digital/account-details/cc/{self._account_uuid}?modal=transactions"
            ),
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
        from_date: datetime,
        export_directory: Path,
    ) -> Path:
        assert self._account_uuid is not None
        export_directory.mkdir(parents=True, exist_ok=True)

        account_url = f"https://www1.bmo.com/banking/digital/account-details/cc/{self._account_uuid}"
        logger.info("Navigating to account details page for web parsing")
        self._navigate("open BMO account details page for web parsing", account_url)

        self._wait_until(
            "load BMO transaction section headers",
            EC.presence_of_element_located((By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_SECTION_HEADER)),
            DelaySeconds.PAGE_TIMEOUT,
            screenshot_name="bmo_transactions_load_timeout",
        )

        all_transactions: list[tuple[datetime, str, float]] = []
        reached_date_limit = False

        while True:
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

            logger.debug(
                "Page parsed: %d txns on page, %d kept total, reached_date_limit=%s",
                len(page_transactions),
                len(all_transactions),
                reached_date_limit,
            )

            if reached_date_limit:
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

            old_row = self._find_element(
                "find current BMO transaction row before paginating",
                By.CSS_SELECTOR,
                ConnectionElementId.TRANSACTION_ROW_INTERACTIVE,
            )
            self._click("click BMO pagination next button", next_btn)

            try:
                self._wait_until(
                    "wait for BMO previous transaction row to become stale after pagination",
                    EC.staleness_of(old_row),
                    DelaySeconds.PAGINATION_WAIT,
                    timeout_log_level=logging.INFO,
                )
            except (TimeoutException, StaleElementReferenceException):
                logger.info("BMO previous-row staleness wait did not complete; checking for new rows anyway")

            try:
                self._wait_until(
                    "load BMO transaction rows after pagination",
                    EC.presence_of_element_located((By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_ROW_INTERACTIVE)),
                    DelaySeconds.PAGINATION_WAIT,
                )
            except TimeoutException:
                logger.warning("Pagination wait timed out — stopping")
                break

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

    def _parse_amount_from_web(self, text: str) -> float:
        cleaned = text.replace("\n", "").strip()
        sign = -1 if "-" in cleaned else 1
        number = re.search(r"[\d,.]+", cleaned)
        if not number:
            raise ValueError(f"Invalid amount: {text}")
        value = float(number.group().replace(",", ""))
        return sign * value

    def _parse_posted_transactions_from_page(self) -> list[tuple[datetime, str, float]]:
        headers = self.driver.find_elements(By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_SECTION_HEADER)
        logger.debug("Found %d section headers: %s", len(headers), [h.text[:50] for h in headers])
        rows = self.driver.find_elements(By.XPATH, ConnectionElementId.TRANSACTION_ROWS)

        transactions = []
        for row in rows:
            date_text = row.find_element(By.XPATH, ConnectionElementId.TRANSACTION_DATE).text
            desc = row.find_element(By.XPATH, ConnectionElementId.TRANSACTION_DESC).text
            amount_raw = row.find_element(By.XPATH, ConnectionElementId.TRANSACTION_AMOUNT).text
            amount = self._parse_amount_from_web(amount_raw)

            try:
                txn_date = datetime.strptime(date_text, "%b %d, %Y")
            except ValueError:
                logger.warning("Could not parse date: %s — skipping row", date_text)
                continue

            transactions.append((txn_date, desc, amount))

        return transactions

    def _login(self, username: SecretString, password: SecretString) -> None:
        logger.info("Filling login credentials")
        username_field = self._find_element("enter BMO username", By.XPATH, ConnectionElementId.USERNAME)
        self._send_keys("enter BMO username", username_field, username.reveal(), sensitive=True)
        password_field = self._find_element("enter BMO password", By.XPATH, ConnectionElementId.PASSWORD)
        self._send_keys("enter BMO password", password_field, password.reveal(), sensitive=True)
        self._dismiss_cookie_banner()
        self._ensure_password_populated(password)
        sign_in_btn = self._find_element("click BMO sign-in button", By.XPATH, ConnectionElementId.SIGN_IN)
        self._click("click BMO sign-in button", sign_in_btn)

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

        if password_value:
            logger.info("BMO password field is populated before sign-in")
            return

        logger.warning("BMO password field was empty before sign-in; re-entering the redacted password")
        try:
            self._send_keys("re-enter BMO password before sign-in", password_field, password.reveal(), sensitive=True)
            password_value = password_field.get_attribute("value")
        except WebDriverException as e:
            self._save_screenshot("bmo_password_reentry_failed")
            raise WebDriverException("Failed while re-entering and verifying BMO password before sign-in") from e

        if not password_value:
            self._save_screenshot("bmo_password_reentry_empty")
            raise WebDriverException("BMO password field remained empty after re-entry before sign-in")

        logger.info("BMO password field is populated after re-entry")

    def _dismiss_cookie_banner(self) -> None:
        try:
            accept_btn = WebDriverWait(self.driver, DelaySeconds.COOKIE_BANNER_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, ConnectionElementId.COOKIE_ACCEPT))
            )
            self._click("dismiss BMO cookie consent banner", accept_btn)
            WebDriverWait(self.driver, DelaySeconds.COOKIE_BANNER_TIMEOUT).until(
                EC.invisibility_of_element_located((By.ID, "onetrust-banner-sdk"))
            )
            logger.info("Cookie consent banner dismissed")
        except TimeoutException:
            logger.info("No cookie consent banner found, proceeding")

    def _wait_for_2fa_or_success(self) -> Literal["2fa", "success"]:
        """Wait for either the 2FA NEXT button or the success URL after credentials are submitted."""
        try:
            self._wait_until(
                "reach BMO success URL or detect BMO 2FA screen after login",
                lambda d: (
                    d.current_url == BMO_SUCCESS_URL
                    or len(d.find_elements(By.XPATH, ConnectionElementId.MFA_NEXT_BUTTON)) > 0
                ),
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

    def _handle_2fa_initiation(self) -> None:
        """Drive the steps to request the OTP code be sent to the user's phone."""
        logger.info("2FA screen detected — clicking NEXT")
        next_btn = self._wait_until(
            "find clickable BMO 2FA next button",
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_NEXT_BUTTON)),
            DelaySeconds.MFA_STEP_TIMEOUT,
        )
        self._click("click BMO 2FA next button", next_btn)

        logger.info("Selecting phone radio button")
        radio = self._wait_until(
            "find clickable BMO 2FA phone radio button",
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_PHONE_RADIO)),
            DelaySeconds.MFA_STEP_TIMEOUT,
        )
        self._click("select BMO 2FA phone radio button", radio)

        logger.info("Ticking the 'I won't share' checkbox")
        checkbox = self._wait_until(
            "find clickable BMO 2FA agreement checkbox",
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_AGREE_CHECKBOX)),
            DelaySeconds.MFA_STEP_TIMEOUT,
        )
        self._click("tick BMO 2FA agreement checkbox", checkbox)

        logger.info("Clicking SEND CODE")
        send_btn = self._wait_until(
            "find clickable BMO send-code button",
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_SEND_CODE)),
            DelaySeconds.MFA_STEP_TIMEOUT,
        )
        self._click("click BMO send-code button", send_btn)

    def _capture_cookies(self) -> None:
        account_url = f"https://www1.bmo.com/banking/digital/account-details/cc/{self._account_uuid}"
        logger.info("Navigating to account page to prime XSRF-TOKEN cookie")
        self._navigate("open BMO account page to prime XSRF token cookie", account_url)
        sleep(DelaySeconds.ACCOUNT_NAV_WAIT)

        self.cookies = self.driver.get_cookies()
        logger.info("Session cookies captured (%d cookies)", len(self.cookies))

    def _extract_login_error(self) -> str | None:
        try:
            banner = self.driver.find_element(By.XPATH, ConnectionElementId.LOGIN_ERROR_BANNER)
            text = banner.text.strip()
            return text if text else None
        except Exception:
            return None

    @staticmethod
    def _parse_filename_from_header(header_value: str) -> str | None:
        match = re.search(r"filename=([^\s;]+)", header_value)
        return match.group(1) if match else None
