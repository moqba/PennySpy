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
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pennyspy.scrapers.base import AuthStep, BankScraper
from pennyspy.scrapers.bmo_bank.connection_element_id import ConnectionElementId
from pennyspy.scrapers.bmo_bank.delay_seconds import DelaySeconds
from pennyspy.scrapers.bmo_bank.get_default_filename import get_default_filename
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate
from pennyspy.scrapers.get_required_env_var import get_required_env_var
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
        logger.info("Navigating to BMO login page")
        self.driver.get(BMO_LOGIN_URL)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)

        username = get_required_env_var("PENNYSPY_BMOU")
        password = get_required_env_var("PENNYSPY_BMOPP")
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
        if kwargs.get("until_date") is not None:
            # Web scraping path — uses the live browser, no cookies needed
            return self._parse_transactions_from_web(
                until_date=kwargs["until_date"],
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
        otp_field = WebDriverWait(self.driver, DelaySeconds.MFA_STEP_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.OTP_INPUT))
        )
        otp_field.send_keys(otp_code)

        confirm_btn = WebDriverWait(self.driver, DelaySeconds.MFA_STEP_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_CONFIRM))
        )
        confirm_btn.click()

        logger.info("Waiting for CONTINUE button")
        continue_btn = WebDriverWait(self.driver, DelaySeconds.TWO_FACTOR_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_CONTINUE))
        )
        continue_btn.click()

        logger.info("Waiting for post-2FA redirect to %s", BMO_SUCCESS_URL)
        WebDriverWait(self.driver, DelaySeconds.LOGIN_SUCCESS_TIMEOUT).until(EC.url_to_be(BMO_SUCCESS_URL))

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
        until_date: datetime,
        export_directory: Path | str,
    ) -> Path:
        assert self._account_uuid is not None

        export_directory = Path(export_directory)
        export_directory.mkdir(parents=True, exist_ok=True)

        account_url = f"https://www1.bmo.com/banking/digital/account-details/cc/{self._account_uuid}"
        logger.info("Navigating to account details page for web parsing")
        self.driver.get(account_url)

        WebDriverWait(self.driver, DelaySeconds.PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_SECTION_HEADER))
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
                if txn_date < until_date:
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
                next_btn = self.driver.find_element(By.CSS_SELECTOR, ConnectionElementId.PAGINATION_NEXT_BUTTON)
            except Exception:
                break

            if next_btn.get_attribute("disabled") is not None:
                break

            old_row = self.driver.find_element(By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_ROW_INTERACTIVE)
            next_btn.click()

            try:
                WebDriverWait(self.driver, DelaySeconds.PAGINATION_WAIT).until(EC.staleness_of(old_row))
            except (TimeoutException, StaleElementReferenceException):
                pass

            try:
                WebDriverWait(self.driver, DelaySeconds.PAGINATION_WAIT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ConnectionElementId.TRANSACTION_ROW_INTERACTIVE))
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

    def _login(self, username: str, password: str) -> None:
        logger.info("Filling login credentials")
        self.driver.find_element(By.XPATH, ConnectionElementId.USERNAME).send_keys(username)
        self.driver.find_element(By.XPATH, ConnectionElementId.PASSWORD).send_keys(password)
        self._dismiss_cookie_banner()
        self.driver.find_element(By.XPATH, ConnectionElementId.SIGN_IN).click()

    def _dismiss_cookie_banner(self) -> None:
        try:
            accept_btn = WebDriverWait(self.driver, DelaySeconds.COOKIE_BANNER_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, ConnectionElementId.COOKIE_ACCEPT))
            )
            accept_btn.click()
            WebDriverWait(self.driver, DelaySeconds.COOKIE_BANNER_TIMEOUT).until(
                EC.invisibility_of_element_located((By.ID, "onetrust-banner-sdk"))
            )
            logger.info("Cookie consent banner dismissed")
        except TimeoutException:
            logger.info("No cookie consent banner found, proceeding")

    def _wait_for_2fa_or_success(self) -> Literal["2fa", "success"]:
        """Wait for either the 2FA NEXT button or the success URL after credentials are submitted."""
        try:
            WebDriverWait(self.driver, DelaySeconds.LOGIN_SUCCESS_TIMEOUT).until(
                lambda d: (
                    d.current_url == BMO_SUCCESS_URL
                    or len(d.find_elements(By.XPATH, ConnectionElementId.MFA_NEXT_BUTTON)) > 0
                )
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
        next_btn = WebDriverWait(self.driver, DelaySeconds.MFA_STEP_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_NEXT_BUTTON))
        )
        next_btn.click()

        logger.info("Selecting phone radio button")
        radio = WebDriverWait(self.driver, DelaySeconds.MFA_STEP_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_PHONE_RADIO))
        )
        radio.click()

        logger.info("Ticking the 'I won't share' checkbox")
        checkbox = WebDriverWait(self.driver, DelaySeconds.MFA_STEP_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_AGREE_CHECKBOX))
        )
        checkbox.click()

        logger.info("Clicking SEND CODE")
        send_btn = WebDriverWait(self.driver, DelaySeconds.MFA_STEP_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, ConnectionElementId.MFA_SEND_CODE))
        )
        send_btn.click()

    def _capture_cookies(self) -> None:
        account_url = f"https://www1.bmo.com/banking/digital/account-details/cc/{self._account_uuid}"
        logger.info("Navigating to account page to prime XSRF-TOKEN cookie")
        self.driver.get(account_url)
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
