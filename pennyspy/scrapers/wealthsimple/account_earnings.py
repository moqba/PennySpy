"""A day-by-day earnings series for a Wealthsimple account, built from its value graph.

Where the numbers come from
---------------------------
The account-details page (``/app/account-details/<account_id>``) draws its "Account value"
chart from one GraphQL operation, ``FetchAccountGraphData``. Each point on that chart
carries two amounts: ``netLiquidationValue`` — what the account was worth at that moment —
and ``netDeposits``, the running total of money moved in and out since the account opened.
Both are needed, because the account value alone cannot tell a $500 deposit apart from
$500 of growth.

So each day's change is split in two:

    value_change = account_value(day) - account_value(previous day)
    net_deposit  = net_deposits(day)  - net_deposits(previous day)
    earnings     = value_change - net_deposit

``earnings`` is what the account did on its own — market moves, dividends, interest, fees —
with deposits and withdrawals taken back out. The first day of the series has no previous
day to subtract, so it produces no row.

One line per movement
---------------------
The two halves are written as separate lines rather than as two columns of one line, each
carrying an ``entry_type`` that says which it is. A day where money went in *and* the market
moved therefore produces two lines sharing one date — a ``deposit`` line and an ``earning``
line — which is the shape a ledger reads.

A half that came to nothing is left out: no line is written for an amount of zero. Most days
carry no deposit at all, and a $0.00 deposit line on every one of them would be noise rather
than information; a day where nothing whatsoever moved contributes no lines. The day-level
figures a line still carries — ``account_value``, ``net_deposits_total``, ``reported`` —
describe the day the line falls on, so they repeat across a day's two lines. ``value_change``
is deliberately not among them: a day total repeated on both of its lines would double as
soon as anyone summed the column, and the total is anyway the sum of the day's own lines.

Gaps in the graph are closed, not skipped
-----------------------------------------
Over a yearly range Wealthsimple already plots one point per calendar date, weekends and
holidays included — a non-trading day repeats the previous close rather than being left out.
So the series normally needs no filling, and a weekend simply comes to nothing.

Any gap that does appear is nonetheless closed the same way Wealthsimple would: the previous
day's value and deposits are carried forward, so the missing day comes to zero and the change
that actually happened lands on the day Wealthsimple reported it. Without that, a missing day
would silently roll two days of change into one line. The ``reported`` column marks the lines
whose day was carried forward here rather than sent by Wealthsimple.

The series stops where Wealthsimple's does. A day's close is published after the day ends,
so the graph runs to yesterday; today gets a row tomorrow, not a copy of yesterday today.

Always a year, trimmed here
---------------------------
The graph is always fetched over Wealthsimple's one-year range, whatever window was asked
for, and the rows are trimmed to that window afterwards. The window comes from the same
"last 3 / 6 / 12 months" choice the activity export offers, and every one of those fits
inside a year, so a shorter range would only save response size — while costing the fixed
window a day of slack: the first requested day needs the day *before* it to subtract, and a
range that stops exactly on the start date is one day too short.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from logging import getLogger
from pathlib import Path
from typing import Any, Final

logger = getLogger(__name__)

WEALTHSIMPLE_GRAPHQL: Final[str] = "https://my.wealthsimple.com/graphql"

GRAPH_OPERATION_NAME: Final[str] = "FetchAccountGraphData"

# Copied verbatim from the operation the account-details page sends, so the server sees the
# document it already knows. ``simpleReturns`` stays switched off — the deposit-adjusted
# change is computed here from netDeposits rather than read off WS's own return figure.
GRAPH_QUERY: Final[str] = """
query FetchAccountGraphData($id: ID!, $currency: Currency!, $timeRange: GraphTimeRange!, $marketSession: GraphMarketSession!, $includeSimpleReturns: Boolean = false) {
  account(id: $id) {
    ...AccountGraphData
    __typename
  }
}

fragment AccountGraphData on Account {
  id
  financials {
    currentCombined(currency: $currency) {
      id
      graphData(timeRange: $timeRange, marketSession: $marketSession) {
        previousClose {
          dateTime
          netLiquidationValue {
            amount
            currency
            __typename
          }
          __typename
        }
        data {
          dateTime
          netLiquidationValue {
            amount
            currency
            __typename
          }
          netDeposits {
            amount
            currency
            __typename
          }
          simpleReturns @include(if: $includeSimpleReturns) {
            amount {
              amount
              currency
              __typename
            }
            rate
            referenceDateTime
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}"""

DEFAULT_CURRENCY: Final[str] = "CAD"

# The chart's session filter. Twenty-four hours is what the page itself requests and is the
# only setting that includes after-hours moves in a day's closing value.
MARKET_SESSION: Final[str] = "TWENTY_FOUR_HOURS"

# An account id reaches Wealthsimple both inside a URL path and inside a GraphQL variable
# (``tfsa-l0re4cur``, ``non-registered-abc123``), so it is held to the shape WS actually
# issues rather than being escaped for two different contexts.
_ACCOUNT_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


# The ``GraphTimeRange`` the chart is always asked for. Wealthsimple also accepts
# ``FIVE_YEARS`` and ``TEN_YEARS``, but those thin the series out to roughly one point a week,
# which would turn a week of growth into one spike and six flat days. A year is both the
# longest range that still plots a point per day and the longest window anything asks for.
GRAPH_TIME_RANGE: Final[str] = "ONE_YEAR"

# How far back that range reaches, in days — the measured figure: a response fetched on
# 2026-08-07 began at 2025-08-06. It is the value the "cannot reach that far" warning turns
# on, so understating it would fire that warning on a request for exactly one year of history.
GRAPH_RANGE_DAYS: Final[int] = 366

# What the chart toolbar labels that range's tab. The toolbar offers 1D, 1W, 1M, 3M, 6M, YTD,
# 1Y and ALL; clicking one is what makes the page draw — and therefore request — that range.
GRAPH_RANGE_TAB: Final[str] = "1Y"


class EntryType(StrEnum):
    """Which half of a day's change a line carries."""

    DEPOSIT = "deposit"
    EARNING = "earning"


CSV_COLUMNS: Final[tuple[str, ...]] = (
    "date",
    "account_id",
    "currency",
    "entry_type",
    "amount",
    "account_value",
    "net_deposits_total",
    "reported",
)

_CENTS: Final[Decimal] = Decimal("0.01")


@dataclass(frozen=True)
class GraphPoint:
    """One plotted point: what the account was worth, and what had been paid into it."""

    moment: datetime
    account_value: Decimal
    net_deposits: Decimal
    currency: str


@dataclass(frozen=True)
class DailyValue:
    """An account's closing position on one calendar day."""

    day: date
    account_value: Decimal
    net_deposits: Decimal
    currency: str
    reported: bool


@dataclass(frozen=True)
class EarningsRow:
    """One line of the file: one half of one day's change, and the day it belongs to."""

    day: date
    account_id: str
    currency: str
    entry_type: EntryType
    amount: Decimal
    account_value: Decimal
    net_deposits_total: Decimal
    reported: bool

    def as_csv_row(self) -> list[str]:
        return [
            self.day.isoformat(),
            self.account_id,
            self.currency,
            str(self.entry_type),
            _format(self.amount),
            _format(self.account_value),
            _format(self.net_deposits_total),
            "true" if self.reported else "false",
        ]


class EarningsDataError(ValueError):
    """The graph response did not carry a usable series for the account."""


def validate_account_id(account_id: str) -> str:
    """``account_id`` stripped, or a ``ValueError`` naming what was rejected."""
    cleaned = account_id.strip()
    if not _ACCOUNT_ID.match(cleaned):
        raise ValueError(
            f"{account_id!r} is not a Wealthsimple account id — expected the id from the "
            "account-details URL, e.g. 'tfsa-l0re4cur'"
        )
    return cleaned


def graph_reaches(since_date: date, *, today: date | None = None) -> bool:
    """Whether the one-year graph reaches back past ``since_date``.

    "Past" rather than "to": the first requested day needs the day before it to subtract, so
    a graph that stops exactly on ``since_date`` is one day too short. A window that reaches
    further back than the graph is not an error — the series simply starts where
    Wealthsimple's data starts — but it is worth saying so.
    """
    reference = today or date.today()
    return reference - timedelta(days=GRAPH_RANGE_DAYS) <= since_date - timedelta(days=1)


def graph_request_body(account_id: str, *, currency: str = DEFAULT_CURRENCY) -> dict[str, Any]:
    """The JSON body for one ``FetchAccountGraphData`` call."""
    return {
        "operationName": GRAPH_OPERATION_NAME,
        "variables": {
            "includeSimpleReturns": False,
            "id": account_id,
            "currency": currency,
            "timeRange": GRAPH_TIME_RANGE,
            "marketSession": MARKET_SESSION,
        },
        "query": GRAPH_QUERY,
    }


def _amount(money: Any) -> Decimal | None:
    """The ``amount`` of a WS ``Money`` object, or None when it is absent or unparseable."""
    if not isinstance(money, Mapping):
        return None
    try:
        return Decimal(str(money["amount"]))
    except (KeyError, TypeError, InvalidOperation):
        return None


def _currency(money: Any, fallback: str) -> str:
    if isinstance(money, Mapping):
        currency = money.get("currency")
        if isinstance(currency, str) and currency:
            return currency
    return fallback


def _moment(raw: Any) -> datetime | None:
    """Parse a WS ``dateTime`` (``2026-08-06T04:00:00.000Z``) into an aware datetime."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_graph_points(payload: Mapping[str, Any], *, currency: str = DEFAULT_CURRENCY) -> list[GraphPoint]:
    """The plotted points of a ``FetchAccountGraphData`` response, oldest first.

    A GraphQL error, or a response shaped differently from the one the page receives, is
    raised as :class:`EarningsDataError` rather than quietly yielding an empty series — an
    empty CSV is indistinguishable from an account that genuinely did nothing.
    """
    errors = payload.get("errors")
    if errors:
        raise EarningsDataError(f"Wealthsimple rejected the account graph query: {_describe_errors(errors)}")

    account = (payload.get("data") or {}).get("account")
    if not isinstance(account, Mapping):
        raise EarningsDataError(
            "Wealthsimple returned no account for the graph query — check that the account id is one "
            "of yours, exactly as it appears in the account-details URL"
        )

    graph = (((account.get("financials") or {}).get("currentCombined") or {}).get("graphData")) or {}
    raw_points = graph.get("data")
    if not isinstance(raw_points, Sequence):
        raise EarningsDataError("The Wealthsimple account graph carried no data points")

    points: list[GraphPoint] = []
    for raw in raw_points:
        if not isinstance(raw, Mapping):
            continue
        moment = _moment(raw.get("dateTime"))
        value = _amount(raw.get("netLiquidationValue"))
        deposits = _amount(raw.get("netDeposits"))
        # netDeposits is what makes the split possible; a point without it cannot be told
        # apart from one where money moved, so it is dropped rather than guessed at.
        if moment is None or value is None or deposits is None:
            continue
        points.append(
            GraphPoint(
                moment=moment,
                account_value=value,
                net_deposits=deposits,
                currency=_currency(raw.get("netLiquidationValue"), currency),
            )
        )

    points.sort(key=lambda point: point.moment)
    return points


def _describe_errors(errors: Any) -> str:
    if isinstance(errors, Sequence) and not isinstance(errors, str):
        messages = [str(e.get("message", e)) if isinstance(e, Mapping) else str(e) for e in errors]
        return "; ".join(messages) or "no message given"
    return str(errors)


def daily_closes(points: Iterable[GraphPoint]) -> list[DailyValue]:
    """The last point of each calendar day, oldest first.

    The day is the point's UTC calendar date. Wealthsimple stamps a day's points from the
    Eastern midnight boundary (``04:00Z``/``05:00Z``) through the close (``21:00Z``), so
    every timestamp belonging to one Eastern trading day already shares one UTC date — no
    timezone database is needed to group them, and none is available in every environment
    this runs in.
    """
    closes: dict[date, DailyValue] = {}
    for point in points:
        day = point.moment.astimezone(UTC).date()
        closes[day] = DailyValue(
            day=day,
            account_value=point.account_value,
            net_deposits=point.net_deposits,
            currency=point.currency,
            reported=True,
        )
    return [closes[day] for day in sorted(closes)]


def fill_missing_days(closes: Sequence[DailyValue]) -> list[DailyValue]:
    """``closes`` with any gap filled by carrying the previous day forward.

    The series is never extended past the last day Wealthsimple reported. Wealthsimple
    publishes a day's close after that day ends, so the graph runs to *yesterday*; carrying
    yesterday's figure into today would put a row in the file for a day that has not been
    valued yet, which is a guess wearing the same clothes as a fact.
    """
    if not closes:
        return []
    end = closes[-1].day

    filled: list[DailyValue] = []
    by_day = {close.day: close for close in closes}
    previous = closes[0]
    day = closes[0].day
    while day <= end:
        close = by_day.get(day)
        if close is None:
            close = DailyValue(
                day=day,
                account_value=previous.account_value,
                net_deposits=previous.net_deposits,
                currency=previous.currency,
                reported=False,
            )
        filled.append(close)
        previous = close
        day += timedelta(days=1)
    return filled


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_CENTS)


def build_earnings_rows(closes: Sequence[DailyValue], account_id: str) -> list[EarningsRow]:
    """The lines for each day of ``closes`` after the first: its deposit, its earnings, or both.

    Amounts are rounded to cents on the way out, and ``earnings`` is derived from the two
    rounded figures rather than rounded itself, so a day's lines add up to exactly the change
    in its account value.

    A half that came to zero writes no line, so a day that only earned contributes one line
    and a day where nothing moved contributes none.
    """
    rows: list[EarningsRow] = []
    for previous, current in zip(closes, closes[1:]):
        value_change = _quantize(current.account_value - previous.account_value)
        net_deposit = _quantize(current.net_deposits - previous.net_deposits)
        earnings = value_change - net_deposit
        # Deposit first: it is money that arrived from outside, and the earnings line is what
        # the account then made on its own.
        for entry_type, amount in ((EntryType.DEPOSIT, net_deposit), (EntryType.EARNING, earnings)):
            if amount == 0:
                continue
            rows.append(
                EarningsRow(
                    day=current.day,
                    account_id=account_id,
                    currency=current.currency,
                    entry_type=entry_type,
                    amount=amount,
                    account_value=_quantize(current.account_value),
                    net_deposits_total=_quantize(current.net_deposits),
                    reported=current.reported,
                )
            )
    return rows


def filter_rows(
    rows: Sequence[EarningsRow],
    *,
    since_date: date | None = None,
    until_date: date | None = None,
) -> list[EarningsRow]:
    """The lines whose day falls inside the requested window, both bounds inclusive."""
    return [
        row
        for row in rows
        if (since_date is None or row.day >= since_date) and (until_date is None or row.day <= until_date)
    ]


def _format(value: Decimal) -> str:
    return f"{value:.2f}"


def daily_earnings(
    payload: Mapping[str, Any],
    *,
    account_id: str,
    since_date: date | None = None,
    until_date: date | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> list[EarningsRow]:
    """The whole pipeline: a graph response in, the lines for the requested window out."""
    points = parse_graph_points(payload, currency=currency)
    if not points:
        raise EarningsDataError(
            f"Wealthsimple plotted no points for account {account_id} — the account may be new or closed"
        )
    closes = fill_missing_days(daily_closes(points))
    return filter_rows(build_earnings_rows(closes, account_id), since_date=since_date, until_date=until_date)


def write_earnings_csv(rows: Sequence[EarningsRow], path: Path) -> Path:
    """Write ``rows`` to ``path`` and return it. The header is written even for no rows."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        writer.writerows(row.as_csv_row() for row in rows)
    logger.info("Wrote %d line(s) of Wealthsimple earnings to %s", len(rows), path.name)
    return path


def earnings_filename(account_id: str, rows: Sequence[EarningsRow]) -> str:
    """``wealthsimple_earnings_<account>_<first day>_<last day>.csv``, or a dated name when empty."""
    if rows:
        span = f"{rows[0].day.isoformat()}_{rows[-1].day.isoformat()}"
    else:
        span = date.today().isoformat()
    return f"wealthsimple_earnings_{account_id}_{span}.csv"
