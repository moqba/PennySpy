from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from bs4 import BeautifulSoup
from pandas import DataFrame

from pennyspy.scrapers.base import AuthStep, ZenBankScraper
from pennyspy.scrapers.get_required_env_var import SecretString, get_required_env_var
from pennyspy.scrapers.scraper import BrowserConfig
from pennyspy.scrapers.wealthsimple.activity_fields import ActivityField
from pennyspy.scrapers.wealthsimple.activity_id import ActivityCss, ActivityXpath
from pennyspy.scrapers.wealthsimple.connection_element_id import ActivityElementXpath, ConnectionElementXpath
from pennyspy.scrapers.wealthsimple.delay_seconds import DelaySeconds
from pennyspy.scrapers.wealthsimple.normalize_financial_data import normalize_financial_df
from pennyspy.scrapers.zen_scraper import By, TimeoutException, clickable, present, url_to_be, visible

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


def parse_button_texts(button_inner_html: str) -> list[str]:
    """Extract non-empty button-header text values in document order.

    Filters by data-fs-privacy-rule="unmask" so the status badge span
    (e.g. "Pending", "In progress") is excluded — it never carries that attribute."""
    soup = BeautifulSoup(button_inner_html, "html.parser")
    return [
        el.get_text(strip=True)
        for el in soup.find_all(["p", "span"], {"data-fs-privacy-rule": "unmask"})
        if el.get_text(strip=True)
    ]


def parse_region_html(region_inner_html: str) -> dict:
    """Extract ActivityField values from the expanded region's innerHTML."""
    soup = BeautifulSoup(region_inner_html, "html.parser")
    activity: dict = {}
    _synthetic = {ActivityField.TICKER, ActivityField.BUTTON_PAYEE, ActivityField.BUTTON_AMOUNT}
    for label in ActivityField:
        if label in _synthetic:
            continue
        label_elem = soup.find(
            ["p", "span"],
            {"data-fs-privacy-rule": "unmask"},
            string=lambda s, lbl=label: s and s.strip() == lbl,
        )
        if label_elem and label_elem.parent and label_elem.parent.parent:
            row_div = label_elem.parent.parent  # p/span -> div.hQERxA -> div.lizokw
            value_div = row_div.find("div", class_="gQehiP")
            if value_div:
                value_el = value_div.find(["p", "span"])
                if value_el and hasattr(value_el, "text"):
                    activity[label.value] = value_el.text.strip()
    return activity


def build_activity_row(button_inner_html: str, region_inner_html: str) -> dict | None:
    """Full per-transaction parse: region fields + button-header enrichment, with Cancelled-skip.

    Returns None if the row should be dropped (Cancelled status)."""
    activity = parse_region_html(region_inner_html)
    if activity.get(ActivityField.STATUS.value) == "Cancelled":
        return None
    meta = _parse_button_header(parse_button_texts(button_inner_html))
    if meta.get("ticker") and not activity.get(ActivityField.TICKER.value):
        activity[ActivityField.TICKER.value] = meta["ticker"]
    if meta.get("payee") and not activity.get(ActivityField.BUTTON_PAYEE.value):
        activity[ActivityField.BUTTON_PAYEE.value] = meta["payee"]
    if meta.get("type") and not activity.get(ActivityField.TYPE.value):
        activity[ActivityField.TYPE.value] = meta["type"]
    if meta.get("button_amount") and not activity.get(ActivityField.BUTTON_AMOUNT.value):
        activity[ActivityField.BUTTON_AMOUNT.value] = meta["button_amount"]
    return activity or None


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


class Wealthsimple(ZenBankScraper):
    def __init__(self, config: BrowserConfig = BrowserConfig()):
        super().__init__(config=config)

    # ── BankScraper interface ──────────────────────────────────────────

    def start_auth(self, **kwargs: Any) -> AuthStep:
        logger.info("Sending Login request")
        self._navigate("open Wealthsimple login page", WEALTHSIMPLE_LOGIN)
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

    @staticmethod
    def _normalize_date(d: datetime | date | None) -> datetime | None:
        if isinstance(d, date) and not isinstance(d, datetime):
            return datetime(d.year, d.month, d.day)
        return d

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        since_date = self._normalize_date(kwargs.get("since_date"))
        df = self.fetch_activity(since_date=since_date)
        normalized = normalize_financial_df(df)
        export_directory = Path(export_directory)
        export_directory.mkdir(parents=True, exist_ok=True)
        suffix = f"_{normalized['Date'].iloc[0].strftime('%Y-%m-%d')}" if not normalized.empty else ""
        csv_path = export_directory / f"wealthsimple_activity{suffix}.csv"
        normalized.to_csv(csv_path, index=False)
        return csv_path

    # ── Internal implementation ────────────────────────────────────────

    def _send_2fa_text(self, otp_code: str):
        otp_field = self._find_element("enter Wealthsimple OTP code", By.XPATH, ConnectionElementXpath.PHONE_2FA)
        if not otp_field:
            raise ValueError("No OTP detected")
        # No readback for the WS OTP field: it masks its value, so verification would
        # always fail, and clearing/re-typing an OTP input can auto-submit or misbehave.
        self._send_keys("enter Wealthsimple OTP code", otp_field, otp_code, sensitive=True)
        time.sleep(1)
        submit_btn = self._find_element("submit Wealthsimple OTP code", By.XPATH, ConnectionElementXpath.SUBMIT)
        self._click("submit Wealthsimple OTP code", submit_btn)
        try:
            self._wait_until(
                "check for Wealthsimple 2FA failure message",
                present(By.XPATH, ConnectionElementXpath.FAILED_2FA),
                DelaySeconds.LOGIN_ATTEMPT,
                timeout_log_level=logging.INFO,
            )
            logger.error("2FA error message appeared")
            raise ValueError("OTP code didn't work")
        except TimeoutException:
            pass
        self._dismiss_passkey_prompt()
        self._wait_until(
            "confirm redirect to Wealthsimple home page",
            url_to_be(WEALTHSIMPLE_HOME),
            DelaySeconds.PAGE_LOADING,
            screenshot_name="wealthsimple_home_redirect_timeout",
        )
        logger.info("Connection successful.")

    def _dismiss_passkey_prompt(self) -> None:
        """Dismiss the optional 'Create passkey' prompt shown after 2FA by clicking
        'Maybe later'. The prompt is not always present, so skip silently if absent."""
        try:
            maybe_later = self._wait_until(
                "find Wealthsimple passkey 'Maybe later' button",
                clickable(By.XPATH, ConnectionElementXpath.PASSKEY_MAYBE_LATER),
                DelaySeconds.LOGIN_ATTEMPT,
                timeout_log_level=logging.INFO,
            )
        except TimeoutException:
            logger.info("No Wealthsimple passkey prompt found — continuing")
            return
        self._click("dismiss Wealthsimple passkey prompt", maybe_later)

    def fetch_activity(self, since_date: datetime | None = None) -> DataFrame:
        self.open_activity()
        return self._expand_and_get_all_activity(since_date=since_date)

    def open_activity(self):
        self._navigate("open Wealthsimple activity page", WEALTHSIMPLE_ACTIVITY)
        # Wait for header buttons rather than the "Load more" button: short histories
        # legitimately have no "Load more" at all.
        try:
            self._wait_until(
                "load Wealthsimple activity header buttons",
                present(By.CSS_SELECTOR, ActivityCss.HEADER_BUTTON),
                DelaySeconds.PAGE_LOADING,
                screenshot_name="wealthsimple_activity_headers_timeout",
            )
        except TimeoutException as e:
            raise TimeoutException("Couldn't find any activity header buttons") from e

    def _get_oldest_visible_activity_date(self) -> datetime | None:
        headers = self.driver.find_elements(By.XPATH, ActivityXpath.DATE_HEADER)
        if not headers:
            return None
        try:
            return datetime.strptime(headers[-1].text.strip(), "%B %d, %Y")
        except ValueError:
            return None

    def _count_header_buttons(self) -> int:
        count = self.driver.execute_script(
            f"return document.querySelectorAll('{ActivityCss.HEADER_BUTTON}').length;"
        )
        return int(count or 0)

    def _load_more_until(self, since_date: datetime) -> None:
        while True:
            oldest = self._get_oldest_visible_activity_date()
            if oldest is None or oldest <= since_date:
                break
            load_more = self.driver.find_elements(By.XPATH, ActivityElementXpath.LOAD_MORE)
            if not load_more:
                break
            prev_count = self._count_header_buttons()
            self._click("click Wealthsimple Load more button", load_more[0], paced=False)
            self._wait_until(
                "load more Wealthsimple activity rows",
                lambda d: self._count_header_buttons() > prev_count,
                DelaySeconds.PAGE_LOADING,
                screenshot_name="wealthsimple_load_more_timeout",
            )

    def _snapshot_rows(self) -> list[tuple[str, bool, datetime | None]]:
        """One DOM pass over date headers and accordion header buttons, in document order.

        Returns ``(region_id, is_expanded, date)`` per activity row. A ``None`` date
        (unparseable or no header seen yet) means the row is treated as in range."""
        raw: list[list] = self.driver.execute_script(
            "const nodes = document.querySelectorAll("
            f"'h2[data-fs-privacy-rule=\"unmask\"], {ActivityCss.HEADER_BUTTON}');\n"
            "const out = [];\n"
            "let currentDate = null;\n"
            "for (const node of nodes) {\n"
            "  if (node.tagName === 'H2') { currentDate = node.textContent.trim(); continue; }\n"
            "  out.push([node.getAttribute('aria-controls'),\n"
            "            node.getAttribute('aria-expanded') === 'true', currentDate]);\n"
            "}\n"
            "return out;"
        ) or []
        rows: list[tuple[str, bool, datetime | None]] = []
        for region_id, expanded, date_text in raw:
            if not region_id:
                continue
            row_date: datetime | None = None
            if date_text:
                try:
                    row_date = datetime.strptime(date_text, "%B %d, %Y")
                except ValueError:
                    pass
            rows.append((region_id, bool(expanded), row_date))
        return rows

    def _expand_rows(self, rows: list[tuple[str, bool, datetime | None]], since_date: datetime | None) -> list[str]:
        """Expand every collapsed in-range row and return the region ids to harvest.

        Rows are addressed by their stable ``aria-controls`` id, never by list position,
        so expansion mutating the DOM cannot shift a click onto another button."""
        kept: list[str] = []
        for region_id, expanded, row_date in rows:
            if since_date and row_date is not None and row_date < since_date:
                continue
            kept.append(region_id)
            if expanded:
                continue
            try:
                button = self._find_element(
                    f"expand Wealthsimple activity region {region_id}",
                    By.CSS_SELECTOR,
                    ActivityCss.HEADER_BUTTON_FOR_REGION.format(region_id=region_id),
                )
                # Re-check right before clicking: clicking an already-expanded row collapses it.
                if button.get_attribute("aria-expanded") == "true":
                    continue
                self._click(f"expand Wealthsimple activity region {region_id}", button, paced=False)
                # Wait for region content to render, not just element presence —
                # "In progress" transactions have a lazily-populated region.
                self._wait_until(
                    f"render Wealthsimple activity details for region {region_id}",
                    present(
                        By.XPATH,
                        f'//*[@id="{region_id}"]//*[(self::p or self::span) and @data-fs-privacy-rule="unmask"]',
                    ),
                    DelaySeconds.ROW_RENDER,
                    timeout_log_level=logging.INFO,
                )
            except Exception as e:
                logger.exception("Failed to expand activity %s: %s", region_id, e)
        return kept

    def _harvest_rows(self, region_ids: list[str]) -> list[tuple[str, str | None, str | None]]:
        """Read every expanded row's button + region innerHTML in one batch DOM pass.

        Single JS call instead of two CDP round trips per row. The payload carries the
        HTML of all rows at once; chunk the id list (~50 per call) if multi-year
        ``since_date`` ranges ever make it too large."""
        if not region_ids:
            return []
        raw: list[list] = self.driver.execute_script(
            f"const ids = {json.dumps(region_ids)};\n"
            "const out = [];\n"
            "for (const id of ids) {\n"
            "  const region = document.getElementById(id);\n"
            "  const button = document.querySelector('button[aria-controls=' + JSON.stringify(id) + ']');\n"
            "  out.push([id, button ? button.innerHTML : null, region ? region.innerHTML : null]);\n"
            "}\n"
            "return out;"
        ) or []
        return [(region_id, button_html, region_html) for region_id, button_html, region_html in raw]

    def _expand_and_get_all_activity(self, since_date: datetime | None = None) -> DataFrame:
        if since_date:
            self._load_more_until(since_date)

        region_ids = self._expand_rows(self._snapshot_rows(), since_date)

        rows: list[dict] = []
        for region_id, button_html, region_html in self._harvest_rows(region_ids):
            if not button_html or not region_html:
                logger.warning("Missing HTML for Wealthsimple activity region %s — skipping", region_id)
                continue
            activity = build_activity_row(button_html, region_html)
            if activity:
                rows.append(activity)

        return DataFrame(rows, columns=[field.value for field in ActivityField])

    def _login(self, username: SecretString, password: SecretString):
        logger.info("logging in...")
        username_field = self._find_element("enter Wealthsimple username", By.XPATH, ConnectionElementXpath.USERNAME)
        self._send_keys_verified("enter Wealthsimple username", username_field, username.reveal(), sensitive=True)
        password_field = self._find_element("enter Wealthsimple password", By.XPATH, ConnectionElementXpath.PASSWORD)
        self._send_keys_verified("enter Wealthsimple password", password_field, password.reveal(), sensitive=True)
        submit_btn = self._find_element("click Wealthsimple login submit button", By.XPATH, ConnectionElementXpath.SUBMIT)
        self._click("click Wealthsimple login submit button", submit_btn)

    def _check_for_wrong_login(self):
        try:
            self._wait_until(
                "check for Wealthsimple invalid-login banner",
                visible(By.XPATH, ConnectionElementXpath.USER_INCORRECT),
                DelaySeconds.LOGIN_ATTEMPT,
                timeout_log_level=logging.INFO,
            )
        except TimeoutException:
            return
        raise ValueError("Username and password seems to be invalid, failed to connect.")
