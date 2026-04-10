from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from bs4 import BeautifulSoup
from pandas import DataFrame
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from pennyspy.scrapers.base import AuthStep, BankScraper
from pennyspy.scrapers.get_required_env_var import get_required_env_var
from pennyspy.scrapers.scraper import BrowserConfig
from pennyspy.scrapers.wealthsimple.activity_fields import ActivityField
from pennyspy.scrapers.wealthsimple.activity_id import ActivityXpath
from pennyspy.scrapers.wealthsimple.connection_element_id import ActivityElementXpath, ConnectionElementXpath
from pennyspy.scrapers.wealthsimple.delay_seconds import DelaySeconds
from pennyspy.scrapers.wealthsimple.normalize_financial_data import normalize_financial_df

WEALTHSIMPLE_ROOT: Final[str] = "https://my.wealthsimple.com"

_INVESTMENT_TYPES: frozenset[str] = frozenset(
    {
        "Limit buy",
        "Limit sell",
        "Market buy",
        "Market sell",
        "Fractional buy",
        "Fractional sell",
        "Dividend",
        "Sold asset",
    }
)

_SELF_NAMED_TYPES: frozenset[str] = frozenset(
    {
        "Interest",
        "ATM fee reimbursement",
        "Non-resident tax",
        "Management fee",
        "Recurring deposit",
    }
)
WEALTHSIMPLE_LOGIN: Final[str] = f"{WEALTHSIMPLE_ROOT}/login"
WEALTHSIMPLE_HOME: Final[str] = f"{WEALTHSIMPLE_ROOT}/app/home"
WEALTHSIMPLE_ACTIVITY: Final[str] = f"{WEALTHSIMPLE_ROOT}/app/activity"

logger = logging.getLogger(__name__)


def _parse_button_header(texts: list[str]) -> dict:
    """Extract ticker, transaction type, payee, and amount from button header <p> texts.

    Button text patterns observed in the wild:
      n=5: [ticker, ticker2, type, account, amount]  e.g. ['AMD', 'AMD', 'Limit buy', 'TFSA', '$516']
      n=4: [ticker, type, account, amount]            e.g. ['VFV', 'Fractional sell', 'TFSA', '$4k']
        or [payee,  type, account, amount]            e.g. ['Landlord', 'Interac e-Transfer', ...]
      n=3: [type,   account, amount]                  e.g. ['Interest', 'Chequing • Main', '$1']
        or [ticker, type,    account]  (no amount yet) e.g. ['GE', 'Dividend', 'TFSA']
    """
    result: dict = {}
    n = len(texts)

    # Extract amount from the last element if it looks like a currency value
    if texts and _looks_like_amount(texts[-1]):
        result["button_amount"] = texts[-1]

    if n >= 5:
        result["ticker"] = texts[0]
        result["type"] = texts[2]
    elif n == 4:
        if texts[1].startswith("From:") or texts[1].startswith("To:"):
            result["type"] = "Transfer"
        else:
            result["type"] = texts[1]
            if texts[1] in _INVESTMENT_TYPES:
                result["ticker"] = texts[0]
            else:
                result["payee"] = texts[0]
    elif n == 3:
        if texts[0] in _SELF_NAMED_TYPES:
            result["type"] = texts[0]
        elif texts[1] in _INVESTMENT_TYPES:
            result["ticker"] = texts[0]
            result["type"] = texts[1]
        else:
            result["type"] = texts[0]

    return result


def _looks_like_amount(text: str) -> bool:
    return bool(re.search(r"\$[\d,]+", text))


class Wealthsimple(BankScraper):
    def __init__(self, config: BrowserConfig = BrowserConfig()):
        super().__init__(config=config)

    # ── BankScraper interface ──────────────────────────────────────────

    def start_auth(self, **kwargs: Any) -> AuthStep:
        logger.info("Sending Login request")
        self.driver.get(WEALTHSIMPLE_LOGIN)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)
        username = get_required_env_var("PENNYSPY_WSU")
        password = get_required_env_var("PENNYSPY_WSP")
        self._login(username, password)
        self._check_for_wrong_login()
        return AuthStep(status="needs_otp", message="Enter the OTP code sent to your phone")

    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        assert otp_code is not None, "OTP code is required for Wealthsimple 2FA"
        self._send_2fa_text(otp_code)
        return AuthStep(status="authenticated")

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        since_date: datetime | None = kwargs.get("since_date")
        df = self.fetch_activity(since_date=since_date)
        normalized = normalize_financial_df(df)
        export_directory = Path(export_directory)
        export_directory.mkdir(parents=True, exist_ok=True)
        csv_path = export_directory / "wealthsimple_activity.csv"
        normalized.to_csv(csv_path, index=False)
        return csv_path

    # ── Internal implementation ────────────────────────────────────────

    def _send_2fa_text(self, otp_code: str):
        otp_field = self.driver.find_element(By.XPATH, ConnectionElementXpath.PHONE_2FA)
        if not otp_field:
            raise ValueError("No OTP detected")
        otp_field.send_keys(otp_code)
        time.sleep(1)
        self.driver.find_element(By.XPATH, ConnectionElementXpath.SUBMIT).click()
        try:
            WebDriverWait(self.driver, DelaySeconds.LOGIN_ATTEMPT).until(
                EC.presence_of_element_located((By.XPATH, ConnectionElementXpath.FAILED_2FA))
            )
            logger.error("2FA error message appeared")
            raise ValueError("OTP code didn't work")
        except TimeoutException:
            pass
        assert self.driver.current_url == WEALTHSIMPLE_HOME, "Failed to connect"
        logger.info("Connection successful.")

    def fetch_activity(self, since_date: datetime | None = None) -> DataFrame:
        self.open_activity()
        return self._expand_and_get_all_activity(since_date=since_date)

    def open_activity(self):
        self.driver.get(WEALTHSIMPLE_ACTIVITY)
        WebDriverWait(self.driver, 20).until(
            EC.visibility_of_element_located((By.XPATH, ActivityElementXpath.LOAD_MORE))
        )

    def _get_oldest_visible_activity_date(self) -> datetime | None:
        headers = self.driver.find_elements(By.XPATH, ActivityXpath.DATE_HEADER)
        if not headers:
            return None
        try:
            return datetime.strptime(headers[-1].text.strip(), "%B %d, %Y")
        except ValueError:
            return None

    def _load_more_until(self, since_date: datetime) -> None:
        while True:
            oldest = self._get_oldest_visible_activity_date()
            if oldest is None or oldest <= since_date:
                break
            load_more = self.driver.find_elements(By.XPATH, ActivityElementXpath.LOAD_MORE)
            if not load_more:
                break
            prev_count = len(self.driver.find_elements(By.XPATH, ActivityXpath.TRANSACTION_EXPENSION))
            load_more[0].click()
            WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
                lambda d: len(d.find_elements(By.XPATH, ActivityXpath.TRANSACTION_EXPENSION)) > prev_count
            )

    def _build_date_index(self) -> dict[str, datetime]:
        elements = self.driver.find_elements(
            By.XPATH, '//h2[@data-fs-privacy-rule="unmask"] | //button[contains(@id, "-header")]'
        )
        result: dict[str, datetime] = {}
        current_date: datetime | None = None
        for el in elements:
            if el.tag_name == "h2":
                try:
                    current_date = datetime.strptime(el.text.strip(), "%B %d, %Y")
                except ValueError:
                    pass
            elif current_date is not None:
                aria_controls = el.get_attribute("aria-controls")
                if aria_controls:
                    result[aria_controls] = current_date
        return result

    def _expand_and_get_all_activity(self, since_date: datetime | None = None) -> DataFrame:
        # Wait for at least one header button to confirm the activity list is loaded
        WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
            EC.presence_of_element_located((By.XPATH, ActivityXpath.TRANSACTION_EXPENSION))
        )

        if since_date:
            self._load_more_until(since_date)

        date_index: dict[str, datetime] = self._build_date_index() if since_date else {}

        rows: list[dict] = []
        index = 0
        while True:
            # Re-fetch buttons each iteration to avoid stale element references
            buttons = self.driver.find_elements(By.XPATH, ActivityXpath.TRANSACTION_EXPENSION)
            if index >= len(buttons):
                break
            button = buttons[index]
            index += 1
            try:
                region_id = button.get_attribute("aria-controls")
                if region_id is None:
                    continue

                # Skip activities older than since_date
                if since_date and region_id in date_index and date_index[region_id] < since_date:
                    continue

                if button.get_attribute("aria-expanded") != "true":
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                    button.click()
                    WebDriverWait(self.driver, DelaySeconds.PAGE_LOADING).until(
                        EC.presence_of_element_located((By.ID, region_id))
                    )
                activity_div = self.driver.find_element(By.ID, region_id)
                activity = self.get_activity_html_soup(activity_div)
                # Enrich with ticker / type / payee from the button header
                btn_ps = button.find_elements(By.XPATH, ".//p")
                btn_texts = [p.text.strip() for p in btn_ps if p.text.strip()]
                meta = _parse_button_header(btn_texts)
                if meta.get("ticker") and not activity.get(ActivityField.TICKER.value):
                    activity[ActivityField.TICKER.value] = meta["ticker"]
                if meta.get("payee") and not activity.get(ActivityField.BUTTON_PAYEE.value):
                    activity[ActivityField.BUTTON_PAYEE.value] = meta["payee"]
                if meta.get("type") and not activity.get(ActivityField.TYPE.value):
                    activity[ActivityField.TYPE.value] = meta["type"]
                if meta.get("button_amount") and not activity.get(ActivityField.BUTTON_AMOUNT.value):
                    activity[ActivityField.BUTTON_AMOUNT.value] = meta["button_amount"]
                if activity:
                    rows.append(activity)
            except Exception as e:
                logger.exception(e)

        return DataFrame(rows, columns=[field.value for field in ActivityField])

    def get_activity(self, activity_div):
        activity = {}
        for label in ActivityField:
            try:
                label_elem = activity_div.find_element(
                    By.XPATH, f'.//p[@data-fs-privacy-rule="unmask" and normalize-space(text())="{label}"]'
                )
                if label_elem:
                    value_elem = label_elem.find_element(By.XPATH, '../../div[@data-fs-privacy-rule="mask"]//p')
                    activity[label.value] = value_elem.text.strip()
            except Exception:
                continue

        return activity

    def get_activity_html_soup(self, activity_div):
        html = activity_div.get_attribute("innerHTML")
        soup = BeautifulSoup(html, "html.parser")
        activity = {}
        _synthetic = {ActivityField.TICKER, ActivityField.BUTTON_PAYEE}
        for label in ActivityField:
            if label in _synthetic:
                continue
            label_elem = soup.find("p", {"data-fs-privacy-rule": "unmask"}, string=lambda s: s and s.strip() == label)
            if label_elem and label_elem.parent and label_elem.parent.parent:
                row_div = label_elem.parent.parent  # p -> div.hQERxA -> div.lizokw
                # Value container uses class "gQehiP" regardless of privacy rule
                value_div = row_div.find("div", class_="gQehiP")
                if value_div:
                    value_p = value_div.find("p")
                    if value_p and hasattr(value_p, "text"):
                        activity[label.value] = value_p.text.strip()
        return activity

    def _login(self, username: str, password: str):
        logger.info("logging in...")
        self.driver.find_element(By.XPATH, ConnectionElementXpath.USERNAME).send_keys(username)
        self.driver.find_element(By.XPATH, ConnectionElementXpath.PASSWORD).send_keys(password)
        self.driver.find_element(By.XPATH, ConnectionElementXpath.SUBMIT).click()

    def _check_for_wrong_login(self):
        try:
            WebDriverWait(self.driver, DelaySeconds.LOGIN_ATTEMPT).until(
                EC.visibility_of_element_located((By.XPATH, ConnectionElementXpath.USER_INCORRECT))
            )
            raise ValueError("Username and password seems to be invalid, failed to connect.")
        except TimeoutException:
            pass
