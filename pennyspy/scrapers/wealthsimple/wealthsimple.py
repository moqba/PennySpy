from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from bs4 import BeautifulSoup
from pandas import DataFrame

from pennyspy.scrapers.base import AuthStep, ZenBankScraper
from pennyspy.scrapers.get_required_env_var import SecretString, get_required_env_var
from pennyspy.scrapers.scraper import BrowserConfig
from pennyspy.scrapers.wealthsimple.account_earnings import (
    DEFAULT_CURRENCY,
    GRAPH_RANGE_TAB,
    GRAPH_TIME_RANGE,
    EarningsDataError,
    EarningsRow,
    EntryType,
    daily_earnings,
    earnings_filename,
    graph_reaches,
    graph_request_body,
    validate_account_id,
    write_earnings_csv,
)
from pennyspy.scrapers.wealthsimple.activity_fields import ActivityField
from pennyspy.scrapers.wealthsimple.activity_id import ActivityCss, ActivityXpath
from pennyspy.scrapers.wealthsimple.connection_element_id import (
    AccountGraphXpath,
    ActivityElementXpath,
    ConnectionElementXpath,
    ExportElementCss,
    ExportElementXpath,
)
from pennyspy.scrapers.wealthsimple.delay_seconds import DelaySeconds
from pennyspy.scrapers.wealthsimple.export_period import (
    PERIOD_MONTHS,
    ExportPeriod,
    select_export_period,
    subtract_months,
)
from pennyspy.scrapers.wealthsimple.graph_client import (
    GRAPHQL_PATH,
    OPERATION_NAME_HEADER,
    build_graph_fetch_expression,
    graph_request_headers,
    select_graph_headers,
)
from pennyspy.scrapers.wealthsimple.normalize_financial_data import normalize_financial_df
from pennyspy.scrapers.wealthsimple.split_by_account_type import split_exports_by_account_type
from pennyspy.scrapers.zen_scraper import (
    By,
    ElementHandle,
    RequestLog,
    ScraperError,
    TimeoutException,
    clickable,
    present,
    url_to_be,
    visible,
)

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
WEALTHSIMPLE_ACCOUNT_DETAILS: Final[str] = f"{WEALTHSIMPLE_ROOT}/app/account-details"

# The chart-toolbar tab that plots what the account was worth. Its sibling, "Returns", plots
# WS's own return figure instead, which is not what the earnings series is built from.
ACCOUNT_VALUE_TAB: Final[str] = "Account value"

# Browser downloads land here, inside the export directory but separate from the
# normalized CSV the scrape returns, so the download wait can treat the directory as
# exclusively its own.
_DOWNLOAD_SUBDIR: Final[str] = "ws_export"

logger = logging.getLogger(__name__)


def _parse_header_date(text: str | None) -> datetime | None:
    """Parse a Wealthsimple activity day-header into a datetime.

    Headers are either absolute (``"July 15, 2026"``) or relative
    (``"Today"`` / ``"Yesterday"``). Anything else (e.g. a section label like
    "Scheduled activities") returns None so the row is treated as undated."""
    if not text:
        return None
    text = text.strip()
    lowered = text.lower()
    if lowered == "today":
        today = datetime.now()
        return datetime(today.year, today.month, today.day)
    if lowered == "yesterday":
        yesterday = datetime.now() - timedelta(days=1)
        return datetime(yesterday.year, yesterday.month, yesterday.day)
    try:
        return datetime.strptime(text, "%B %d, %Y")
    except ValueError:
        return None


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


def _extract_row_value(label_elem: Any) -> str | None:
    """Return the value paired with a detail-row label element.

    WS renders each detail row as two sibling cells inside a row container:
    a label cell (holding the ``data-fs-privacy-rule="unmask"`` label) followed
    by a value cell::

        <div>                          <- row container
          <div><span unmask>Account</span></div>          <- label cell
          <div><span>Wealthsimple credit card</span></div><- value cell
        </div>

    The wrapping ``div`` class names are hashed by styled-components and change
    on every WS deploy, so navigate by structure — the label cell's next
    sibling ``div`` — instead of by class name."""
    label_cell = label_elem.parent
    if label_cell is None:
        return None
    value_cell = label_cell.find_next_sibling("div")
    if value_cell is None:
        return None
    value_el = value_cell.find(["p", "span"])
    text = (value_el.text if value_el else value_cell.get_text()).strip()
    return text or None


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
        if not label_elem:
            continue
        value = _extract_row_value(label_elem)
        if value is not None:
            activity[label.value] = value
    return activity


def build_activity_row(
    button_inner_html: str, region_inner_html: str, header_date: datetime | None = None
) -> dict | None:
    """Full per-transaction parse: region fields + button-header enrichment, with Cancelled-skip.

    ``header_date`` is the transaction's day-header date (from the activity feed's
    ``<h3>`` grouping). It's used as a fallback for the ``Date`` field, which some
    transaction types (e.g. credit-card purchases) omit from their expanded region.

    Returns None if the row should be dropped (Cancelled status)."""
    activity = parse_region_html(region_inner_html)
    if activity.get(ActivityField.STATUS.value) == "Cancelled":
        return None
    if not activity.get(ActivityField.DATE.value) and header_date is not None:
        activity[ActivityField.DATE.value] = header_date.strftime("%B %d, %Y")
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

    def download_transaction_files(
        self,
        *,
        export_directory: Path,
        account_ids: Sequence[str] = (),
        **kwargs: Any,
    ) -> list[Path]:
        """Wealthsimple's own activity exports, served through unchanged.

        The downloaded CSVs keep every column and row exactly as WS wrote them — no column
        mapping, no row filtering, no repacking — so the caller sees the bank's native
        export. Wealthsimple exports one file per account, so this is usually several
        files; a file that still mixes account types is split along its ``account_type``
        column (see :mod:`pennyspy.scrapers.wealthsimple.split_by_account_type`).

        ``account_ids`` is optional and asks for a second kind of file alongside the export:
        a daily earnings series per account, covering the same window (see
        :meth:`download_account_earnings`). It rides along with the activity export rather
        than being its own download because both cost the same login and the same OTP, and
        the activity export cannot say what an account earned — it lists what moved, not what
        the market did. The ids are validated before anything is downloaded, so a mistyped
        one is reported straight away instead of after several minutes of scraping.
        """
        since_date = self._normalize_date(kwargs.get("since_date"))
        accounts = self._unique_account_ids(account_ids) if account_ids else []
        export_directory = Path(export_directory)
        export_directory.mkdir(parents=True, exist_ok=True)

        try:
            downloads = self._export_activity_csvs(
                since_date=since_date, download_directory=export_directory / _DOWNLOAD_SUBDIR
            )
        except ScraperError as e:
            # The export dialog is a recent WS addition; a missing step degrades the scrape
            # to the previous activity-feed parsing rather than failing it.
            logger.warning("Wealthsimple CSV export was unavailable (%s); falling back to activity-feed scraping", e)
            self._save_screenshot("wealthsimple_csv_export_unavailable")
            downloads = [self._write_scraped_activity(export_directory=export_directory, since_date=since_date)]
        else:
            downloads = split_exports_by_account_type(downloads)

        downloads += self._earnings_alongside_activity(
            export_directory=export_directory,
            account_ids=accounts,
            since_date=since_date,
        )
        logger.info(
            "Returning %d Wealthsimple export(s): %s",
            len(downloads),
            ", ".join(path.name for path in downloads),
        )
        return downloads

    def _earnings_alongside_activity(
        self,
        *,
        export_directory: Path,
        account_ids: Sequence[str],
        since_date: datetime | None,
    ) -> list[Path]:
        """The earnings CSVs for ``account_ids``, or none of them if the graph cannot be read.

        A graph that will not answer must not cost the activity export, which by this point
        has already been downloaded and is what most of the request was for. The failure is
        logged and the activity CSVs are served on their own.
        """
        if not account_ids:
            return []
        try:
            return self.download_account_earnings(
                export_directory=export_directory,
                account_ids=account_ids,
                since_date=since_date,
            )
        except (ScraperError, EarningsDataError):
            logger.exception(
                "Could not build the Wealthsimple earnings series for %s; serving the activity export alone",
                ", ".join(account_ids),
            )
            return []

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        """Single-file view of the export, for callers bound to one path.

        Bundling is only a way to satisfy that one-path contract — nothing over HTTP takes
        this route, so the web UI never receives a ZIP. Prefer
        :meth:`download_transaction_files`, which hands back the CSVs themselves.
        """
        downloads = self.download_transaction_files(export_directory=export_directory, **kwargs)
        if len(downloads) == 1:
            return downloads[0]
        return self._bundle_exports(downloads, Path(export_directory))

    def _bundle_exports(self, downloads: list[Path], export_directory: Path) -> Path:
        """Bundle several per-account exports into one ZIP, each file byte-for-byte unchanged."""
        zip_path = export_directory / f"wealthsimple_activity_{datetime.now().strftime('%Y-%m-%d')}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in downloads:
                archive.write(path, arcname=path.name)
        logger.info("Bundled %d Wealthsimple export(s) into %s", len(downloads), zip_path)
        return zip_path

    def _write_scraped_activity(self, *, export_directory: Path, since_date: datetime | None) -> Path:
        """Fallback output: the activity feed parsed into PennySpy's normalized schema."""
        normalized = normalize_financial_df(self.fetch_activity(since_date=since_date))
        suffix = f"_{normalized['Date'].iloc[0].strftime('%Y-%m-%d')}" if not normalized.empty else ""
        csv_path = export_directory / f"wealthsimple_activity{suffix}.csv"
        normalized.to_csv(csv_path, index=False)
        return csv_path

    # ── Daily earnings ─────────────────────────────────────────────────

    @staticmethod
    def _as_date(value: datetime | date | None) -> date | None:
        return value.date() if isinstance(value, datetime) else value

    @staticmethod
    def _unique_account_ids(account_ids: Sequence[str]) -> list[str]:
        """The requested ids, validated, blanks dropped, duplicates removed, order kept."""
        unique: list[str] = []
        for raw in account_ids:
            if not str(raw).strip():
                continue
            account_id = validate_account_id(str(raw))
            if account_id not in unique:
                unique.append(account_id)
        if not unique:
            raise ValueError("At least one Wealthsimple account id is required to build an earnings CSV")
        return unique

    def download_account_earnings(
        self,
        *,
        export_directory: Path,
        account_ids: Sequence[str],
        since_date: datetime | date | None = None,
        until_date: datetime | date | None = None,
        currency: str = DEFAULT_CURRENCY,
        **kwargs: Any,
    ) -> list[Path]:
        """One CSV per account, each day's change written as a deposit line, an earnings line, or both.

        The columns and how each day is derived are described in
        :mod:`pennyspy.scrapers.wealthsimple.account_earnings`; the short version is that a
        day's change is split into the money that moved in or out and what the account earned
        once that movement is taken back out, and each half that came to something is written
        as its own line under the date it happened on.

        An account that cannot be read does not take the others down with it: the failure is
        logged with the account it belongs to and the remaining accounts are still written,
        since every account here cost the same login and OTP. Nothing being readable is an
        error.
        """
        accounts = self._unique_account_ids(account_ids)
        today = date.today()
        since = self._as_date(since_date) or subtract_months(today, PERIOD_MONTHS[ExportPeriod.LAST_12_MONTHS])
        # A day past today has no value to report, and asking for one would only forward-fill
        # today's figure into the future as if it were real.
        until = min(self._as_date(until_date) or today, today)
        if until < since:
            raise ValueError(f"The earnings window ends before it starts: {since} to {until}")

        if not graph_reaches(since, today=today):
            logger.warning(
                "since_date %s predates the one-year Wealthsimple account graph; the CSV can "
                "only start where that graph does",
                since,
            )

        export_directory = Path(export_directory)
        export_directory.mkdir(parents=True, exist_ok=True)

        graph_headers = self._prime_graph_headers(accounts)

        written: list[Path] = []
        failures: dict[str, str] = {}
        for account_id in accounts:
            try:
                payload = self._fetch_account_graph(account_id, currency, graph_headers)
                rows = daily_earnings(
                    payload,
                    account_id=account_id,
                    since_date=since,
                    until_date=until,
                    currency=currency,
                )
            except (EarningsDataError, ScraperError) as e:
                logger.exception("Failed to build the Wealthsimple earnings series for account %s", account_id)
                failures[account_id] = str(e)
                continue
            written.append(write_earnings_csv(rows, export_directory / earnings_filename(account_id, rows)))
            self._log_earnings_summary(account_id, rows)

        if failures and not written:
            raise ScraperError(
                "Could not read any Wealthsimple account graph: "
                + "; ".join(f"{account_id}: {reason}" for account_id, reason in failures.items())
            )
        if failures:
            logger.warning(
                "Serving %d of %d Wealthsimple earnings file(s); no data for: %s",
                len(written),
                len(accounts),
                ", ".join(failures),
            )
        return written

    @staticmethod
    def _log_earnings_summary(account_id: str, rows: list[EarningsRow]) -> None:
        if not rows:
            logger.warning(
                "Wealthsimple account %s produced no earnings lines in the requested window — no money "
                "moved and its value did not change",
                account_id,
            )
            return
        days = {row.day for row in rows}
        carried = len({row.day for row in rows if not row.reported})
        logger.info(
            "Wealthsimple account %s: %s to %s, %d line(s) over %d day(s)%s, earnings %s, net deposits %s",
            account_id,
            rows[0].day,
            rows[-1].day,
            len(rows),
            len(days),
            f" ({carried} carried forward)" if carried else "",
            sum(row.amount for row in rows if row.entry_type is EntryType.EARNING),
            sum(row.amount for row in rows if row.entry_type is EntryType.DEPOSIT),
        )

    def _prime_graph_headers(self, account_ids: Sequence[str]) -> dict[str, str]:
        """Get the app to make its own GraphQL requests, and borrow the credentials off one.

        The browser records every request to the GraphQL endpoint for the whole of this step,
        so the recording does not depend on anything landing inside the page and is not lost
        when the page navigates. Each account is opened in turn because the first id may be
        one the user mistyped, whose page never gets as far as querying a graph.

        Only the graph operation itself is worth opening more pages for: it is the one whose
        ``x-ws-operation-hash`` matches the document being replayed. Any other authenticated
        request does just as well otherwise, so once every account has been tried the best
        recording of whatever kind is taken.

        Returns the header set to replay the earnings query with.
        """
        log = self._record_requests("capture the Wealthsimple GraphQL credentials", GRAPHQL_PATH)
        try:
            return self._await_graph_headers(log, account_ids)
        finally:
            self._stop_recording_requests(log)

    def _await_graph_headers(self, log: RequestLog, account_ids: Sequence[str]) -> dict[str, str]:
        for account_id in account_ids:
            self._open_account_graph(account_id)
            try:
                self._wait_until(
                    f"observe the Wealthsimple account-graph request for {account_id}",
                    lambda _driver: self._graph_operation_seen(log) or None,
                    DelaySeconds.GRAPH_HEADERS,
                    timeout_log_level=logging.INFO,
                )
                logger.info("Captured the Wealthsimple account-graph request headers from %s", account_id)
                break
            except TimeoutException:
                logger.info(
                    "The account-details page for %s made no account-graph request within %ss — %s",
                    account_id,
                    int(DelaySeconds.GRAPH_HEADERS),
                    log.describe(interesting=OPERATION_NAME_HEADER),
                )

        selected = select_graph_headers(log.captures())
        if selected is None:
            logger.error("Wealthsimple GraphQL request recording came up empty — %s", log.describe())
            self._save_screenshot("wealthsimple_graph_headers_missing")
            raise ScraperError(
                "The browser recorded no request at all to the Wealthsimple GraphQL endpoint, so "
                f"the account graph cannot be queried — the session may have been signed out ({log.describe()})"
            )

        headers, is_graph_operation = selected
        if not is_graph_operation:
            # The credentials are the app's, not the operation's: any request to the endpoint
            # carries them. Only the per-operation hash is lost, and the query is sent in full
            # so the server does not need it.
            logger.warning(
                "No account-graph request was recorded; replaying with the credentials of another "
                "Wealthsimple GraphQL request (%s)",
                log.describe(interesting=OPERATION_NAME_HEADER),
            )
        return graph_request_headers(headers, is_graph_operation=is_graph_operation)

    @staticmethod
    def _graph_operation_seen(log: RequestLog) -> bool:
        selected = select_graph_headers(log.captures())
        return bool(selected and selected[1])

    def _open_account_graph(self, account_id: str) -> None:
        self._navigate(
            f"open the Wealthsimple account-details page for {account_id}",
            f"{WEALTHSIMPLE_ACCOUNT_DETAILS}/{account_id}",
        )
        self._select_graph_view(account_id)

    def _select_graph_view(self, account_id: str) -> None:
        """Put the account-details chart on the account-value graph over the queried range.

        Left alone the page opens on a one-day chart, so the request the recorder sees is a
        ``ONE_DAY`` one. Clicking the two tabs the chart toolbar offers — the "Account value"
        metric, then the one-year range — has the page issue the very request the earnings
        query goes on to replay, ``x-ws-operation-hash`` included, and proves the account can
        actually draw that range before anything is asked of it.

        The metric is clicked first: switching what the chart plots is what redraws the
        toolbar, and doing it second could put the range back where it started.

        Neither click is required. The earnings query carries its own ``timeRange`` and the
        recorder keeps whatever ``FetchAccountGraphData`` it sees, so a tab WS has renamed or
        dropped costs the tidier priming request rather than the scrape — it is logged and
        the wait for the headers goes ahead regardless.
        """
        self._click_graph_tab(account_id, ACCOUNT_VALUE_TAB)
        self._click_graph_tab(account_id, GRAPH_RANGE_TAB)

    def _click_graph_tab(self, account_id: str, label: str) -> None:
        """Click the chart-toolbar tab labelled ``label``, or log why it could not be."""
        try:
            tab = self._wait_until(
                f"find the Wealthsimple '{label}' chart tab for {account_id}",
                clickable(By.XPATH, AccountGraphXpath.CHART_TAB.format(label=label)),
                DelaySeconds.GRAPH_TAB,
                timeout_log_level=logging.INFO,
            )
            self._click(f"select the Wealthsimple '{label}' chart tab", tab)
        except (TimeoutException, ScraperError):
            logger.info(
                "Could not select the '%s' chart tab on the account-details page for %s; "
                "the graph query does not depend on it",
                label,
                account_id,
            )

    def _fetch_account_graph(self, account_id: str, currency: str, headers: dict[str, str]) -> dict[str, Any]:
        """Query one account's value graph from inside the page and return the JSON body.

        The request goes out from the page rather than from Python so that it carries the
        session cookies and the app's own origin; ``headers`` supplies the rest of what the
        app stamps on such a request."""
        result = self._evaluate_async_script(
            f"query the Wealthsimple {GRAPH_TIME_RANGE} account graph for {account_id}",
            build_graph_fetch_expression(
                graph_request_body(account_id, currency=currency),
                headers=headers,
                timeout_seconds=int(DelaySeconds.GRAPH_REQUEST),
            ),
        )
        if not isinstance(result, dict):
            raise ScraperError(
                f"The Wealthsimple account graph query for {account_id} returned {type(result).__name__}, "
                "not a result object"
            )
        payload = result.get("payload")
        if not result.get("ok") or not isinstance(payload, dict):
            raise ScraperError(
                f"The Wealthsimple account graph query for {account_id} failed "
                f"(status {result.get('status', 'none')}): {result.get('error') or result.get('body') or 'no body'}"
            )
        return payload

    # ── Activity export ────────────────────────────────────────────────

    def _export_activity_csvs(self, *, since_date: datetime | None, download_directory: Path) -> list[Path]:
        """Drive the activity page's "Download activities" dialog and return the saved CSVs."""
        self._navigate("open Wealthsimple activity page", WEALTHSIMPLE_ACTIVITY)
        download_button = self._wait_until(
            "find the Wealthsimple 'Download activities' button",
            clickable(By.CSS_SELECTOR, ExportElementCss.DOWNLOAD_ACTIVITIES),
            DelaySeconds.PAGE_LOADING,
            timeout_log_level=logging.INFO,
        )
        self._set_download_directory(download_directory)
        self._click("open the Wealthsimple activity export dialog", download_button)

        requested_since = since_date.date() if since_date else None
        period = select_export_period(requested_since)
        if requested_since is not None and subtract_months(date.today(), PERIOD_MONTHS[period]) > requested_since:
            logger.warning(
                "since_date %s predates the longest Wealthsimple export window (%s); "
                "the export can only reach back that far",
                requested_since,
                period.value,
            )
        self._select_export_period(period)

        next_button = self._wait_until(
            "find the Wealthsimple export 'Next' button",
            clickable(By.CSS_SELECTOR, ExportElementCss.NEXT),
            DelaySeconds.EXPORT_STEP,
            screenshot_name="wealthsimple_export_next_missing",
        )
        self._click("advance the Wealthsimple export to account selection", next_button)

        account_count = self._select_all_export_accounts()

        csv_button = self._wait_until(
            "find the enabled Wealthsimple 'Download CSV' button",
            clickable(By.CSS_SELECTOR, ExportElementCss.DOWNLOAD_CSV),
            DelaySeconds.EXPORT_STEP,
            screenshot_name="wealthsimple_export_download_disabled",
        )
        self._click("start the Wealthsimple activity CSV download", csv_button)

        # WS exports one CSV per selected account, so the account count is how many files
        # to wait for. It is only a hint: WS sometimes answers with a single combined CSV,
        # and the wait returns whatever actually arrived.
        downloads = self._wait_for_downloads(
            "download the Wealthsimple activity CSVs",
            download_directory,
            timeout=DelaySeconds.DOWNLOAD_TIMEOUT,
            settle_seconds=DelaySeconds.DOWNLOAD_SETTLE,
            expected_files=account_count or None,
            incomplete_settle_seconds=DelaySeconds.DOWNLOAD_INCOMPLETE_SETTLE,
        )
        if account_count and len(downloads) != account_count:
            logger.warning(
                "Wealthsimple exported %d file(s) for %d selected account(s)",
                len(downloads),
                account_count,
            )
        return downloads

    def _selected_export_period(self) -> str:
        text = self.driver.execute_script(
            f"const el = document.querySelector('{ExportElementCss.PERIOD_SELECTOR}');\n"
            "return el ? (el.innerText || el.textContent || '') : '';"
        )
        return str(text or "")

    def _select_export_period(self, period: ExportPeriod) -> None:
        """Pick ``period`` in the export dialog's "Select period" dropdown."""
        selector = self._wait_until(
            "find the Wealthsimple export period selector",
            clickable(By.CSS_SELECTOR, ExportElementCss.PERIOD_SELECTOR),
            DelaySeconds.EXPORT_STEP,
            screenshot_name="wealthsimple_export_period_selector_missing",
        )
        if period.value in self._selected_export_period():
            logger.info("Wealthsimple export period is already %r", period.value)
            return

        self._click("open the Wealthsimple export period dropdown", selector)
        option = self._find_export_period_option(period)
        self._click(f"select the Wealthsimple export period {period.value!r}", option)
        try:
            self._wait_until(
                f"confirm the Wealthsimple export period is {period.value!r}",
                lambda _driver: True if period.value in self._selected_export_period() else None,
                DelaySeconds.ACTION_REFRESH,
                timeout_log_level=logging.INFO,
            )
        except TimeoutException:
            logger.warning(
                "Wealthsimple export period selector still reads %r after choosing %r; continuing",
                self._selected_export_period().replace("\n", " "),
                period.value,
            )

    def _find_export_period_option(self, period: ExportPeriod) -> ElementHandle:
        candidates = (
            (ExportElementXpath.PERIOD_OPTION, DelaySeconds.EXPORT_STEP),
            (ExportElementXpath.PERIOD_OPTION_IN_LISTBOX, DelaySeconds.ACTION_REFRESH),
        )
        for template, timeout in candidates:
            locator = str(template).format(label=period.value)
            try:
                return self._wait_until(
                    f"find the Wealthsimple export period option {period.value!r}",
                    clickable(By.XPATH, locator),
                    timeout,
                    timeout_log_level=logging.INFO,
                )
            except TimeoutException:
                logger.info("Wealthsimple export period option not matched by %s", locator)
        self._save_screenshot("wealthsimple_export_period_option_missing")
        raise TimeoutException(
            f"Wealthsimple export period option {period.value!r} was not found in the period dropdown"
        )

    def _export_account_rows(self) -> list[ElementHandle]:
        return self.driver.find_elements(By.CSS_SELECTOR, ExportElementCss.ACCOUNT_ROW)

    def _export_account_counts(self) -> tuple[int, int]:
        """``(selectable rows, rows still unticked)`` in the account-selection step."""
        counts = self.driver.execute_script(
            f"const rows = Array.from(document.querySelectorAll('{ExportElementCss.ACCOUNT_ROW}'));\n"
            "return [rows.length, rows.filter(el => el.getAttribute('aria-checked') !== 'true').length];"
        ) or [0, 0]
        return int(counts[0]), int(counts[1])

    def _select_all_export_accounts(self) -> int:
        """Tick the "All accounts" master checkbox, falling back to per-account rows.

        Returns how many accounts ended up selected — the number of CSVs the export is
        expected to produce (0 when the rows never rendered)."""
        all_accounts = self._wait_until(
            "find the Wealthsimple export 'All accounts' checkbox",
            clickable(By.XPATH, ExportElementXpath.ALL_ACCOUNTS_CHECKBOX),
            DelaySeconds.EXPORT_STEP,
            screenshot_name="wealthsimple_export_all_accounts_missing",
        )
        if all_accounts.get_attribute("aria-checked") != "true":
            self._click("select all Wealthsimple accounts for export", all_accounts)

        # The account rows render asynchronously, so "nothing unticked" only counts as
        # success once at least one selectable row exists.
        def every_account_selected(_driver: Any) -> bool | None:
            total, unchecked = self._export_account_counts()
            return True if total and not unchecked else None

        try:
            self._wait_until(
                "confirm every Wealthsimple account is selected for export",
                every_account_selected,
                DelaySeconds.EXPORT_STEP,
                timeout_log_level=logging.INFO,
            )
            total, _ = self._export_account_counts()
            logger.info("Selected all %d Wealthsimple account(s) for export", total)
            return total
        except TimeoutException:
            total, unchecked = self._export_account_counts()
            logger.info(
                "'All accounts' left %d of %d Wealthsimple account row(s) unticked — selecting them individually",
                unchecked,
                total,
            )

        for index, row in enumerate(self._export_account_rows()):
            if row.get_attribute("aria-checked") == "true":
                continue
            self._click(f"select Wealthsimple export account row {index}", row, paced=False)
        total, unchecked = self._export_account_counts()
        if unchecked:
            self._save_screenshot("wealthsimple_export_accounts_unselected")
            raise TimeoutException(
                f"{unchecked} of {total} Wealthsimple account row(s) could not be selected for export"
            )
        logger.info("Selected all %d Wealthsimple account(s) for export individually", total)
        return total

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
        return _parse_header_date(headers[-1].text)

    def _count_header_buttons(self) -> int:
        count = self.driver.execute_script(f"return document.querySelectorAll('{ActivityCss.HEADER_BUTTON}').length;")
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
        raw: list[list] = (
            self.driver.execute_script(
                "const nodes = document.querySelectorAll("
                f"'h3[data-fs-privacy-rule=\"unmask\"], {ActivityCss.HEADER_BUTTON}');\n"
                "const out = [];\n"
                "let currentDate = null;\n"
                "for (const node of nodes) {\n"
                "  if (node.tagName === 'H3') { currentDate = node.textContent.trim(); continue; }\n"
                "  out.push([node.getAttribute('aria-controls'),\n"
                "            node.getAttribute('aria-expanded') === 'true', currentDate]);\n"
                "}\n"
                "return out;"
            )
            or []
        )
        rows: list[tuple[str, bool, datetime | None]] = []
        for region_id, expanded, date_text in raw:
            if not region_id:
                continue
            rows.append((region_id, bool(expanded), _parse_header_date(date_text)))
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
        raw: list[list] = (
            self.driver.execute_script(
                f"const ids = {json.dumps(region_ids)};\n"
                "const out = [];\n"
                "for (const id of ids) {\n"
                "  const region = document.getElementById(id);\n"
                "  const button = document.querySelector('button[aria-controls=' + JSON.stringify(id) + ']');\n"
                "  out.push([id, button ? button.innerHTML : null, region ? region.innerHTML : null]);\n"
                "}\n"
                "return out;"
            )
            or []
        )
        return [(region_id, button_html, region_html) for region_id, button_html, region_html in raw]

    def _expand_and_get_all_activity(self, since_date: datetime | None = None) -> DataFrame:
        if since_date:
            self._load_more_until(since_date)

        snapshot = self._snapshot_rows()
        date_by_region = {region_id: row_date for region_id, _, row_date in snapshot}
        region_ids = self._expand_rows(snapshot, since_date)

        rows: list[dict] = []
        for region_id, button_html, region_html in self._harvest_rows(region_ids):
            if not button_html or not region_html:
                logger.warning("Missing HTML for Wealthsimple activity region %s — skipping", region_id)
                continue
            activity = build_activity_row(button_html, region_html, header_date=date_by_region.get(region_id))
            if activity:
                rows.append(activity)

        return DataFrame(rows, columns=[field.value for field in ActivityField])

    def _login(self, username: SecretString, password: SecretString):
        logger.info("logging in...")
        username_field = self._find_element("enter Wealthsimple username", By.XPATH, ConnectionElementXpath.USERNAME)
        self._send_keys_verified("enter Wealthsimple username", username_field, username.reveal(), sensitive=True)
        password_field = self._find_element("enter Wealthsimple password", By.XPATH, ConnectionElementXpath.PASSWORD)
        self._send_keys_verified("enter Wealthsimple password", password_field, password.reveal(), sensitive=True)
        submit_btn = self._find_element(
            "click Wealthsimple login submit button", By.XPATH, ConnectionElementXpath.SUBMIT
        )
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
