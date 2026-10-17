"""Turn the rows of NBC's transaction history into one CSV per account.

Kept apart from the browser code so the parsing and the file split can be tested offline. The
rows come from the table on ``app.bnc.ca/accounts`` (Transaction history tab), read by
:meth:`pennyspy.scrapers.nbc.nbc.NationalBank._read_rows` as plain strings.
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

CSV_COLUMNS: Final[tuple[str, ...]] = ("date", "description", "category", "amount", "account")

# Every character a rendered amount can carry besides its digits: currency, thousands
# separators and the spaces (regular, no-break, narrow no-break) between them.
_AMOUNT_NOISE: Final[re.Pattern[str]] = re.compile(r"[$,\s  ]|CAD", re.IGNORECASE)
_NON_ALNUM: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


class TransactionParseError(ValueError):
    """A row of the table could not be read; the message names the field and its text."""


@dataclass(frozen=True)
class Transaction:
    date: date
    description: str
    category: str
    amount: Decimal
    account: str


def parse_date(text: str, *, today: date) -> date:
    """``"October 14, 2026"`` → ``date(2026, 10, 14)``. "Today" and "Yesterday" are also read,
    in case the table ever shows them for recent rows."""
    cleaned = " ".join(text.split())
    lowered = cleaned.lower()
    if lowered == "today":
        return today
    if lowered == "yesterday":
        return today - timedelta(days=1)
    for pattern in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    raise TransactionParseError(f"Unreadable NBC transaction date {cleaned!r}")


def parse_amount(text: str, *, credit: bool, debit: bool) -> Decimal:
    """``"$5,000.00"`` → ``Decimal("5000.00")``, negative for a debit.

    NBC renders every amount without a sign and marks the direction with the ``is-credit`` /
    ``is-debit`` class instead. A sign or parentheses in the text are honoured when neither
    class is there."""
    cleaned = _AMOUNT_NOISE.sub("", text)
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned, negative = cleaned[1:-1], True
    if cleaned.startswith(("-", "−")):
        cleaned, negative = cleaned[1:], True
    elif cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        value = Decimal(cleaned)
    except InvalidOperation as e:
        raise TransactionParseError(f"Unreadable NBC transaction amount {text!r}") from e
    if debit:
        return -abs(value)
    if credit:
        return abs(value)
    return -value if negative else value


def parse_rows(rows: Iterable[Mapping[str, object]], *, today: date) -> list[Transaction]:
    """Rows as read from the page (see ``_ROWS_SCRIPT`` in ``nbc.py``) → transactions, in order."""
    transactions: list[Transaction] = []
    unmarked = 0
    for index, row in enumerate(rows, start=1):
        credit, debit = bool(row.get("credit")), bool(row.get("debit"))
        if not credit and not debit:
            unmarked += 1
        try:
            transactions.append(
                Transaction(
                    date=parse_date(str(row.get("date") or ""), today=today),
                    description=_clean(row.get("description")),
                    category=_clean(row.get("category")),
                    amount=parse_amount(str(row.get("amount") or ""), credit=credit, debit=debit),
                    account=_clean(row.get("account")),
                )
            )
        except TransactionParseError as e:
            raise TransactionParseError(f"Row {index} of the NBC transaction table: {e}") from e
    if unmarked:
        logger.warning("%d NBC row(s) had neither is-credit nor is-debit; their sign comes from the text", unmarked)
    return transactions


def keep_since(transactions: Iterable[Transaction], since: date) -> list[Transaction]:
    """The transactions on or after ``since``, in their original order."""
    return [t for t in transactions if t.date >= since]


def account_slug(name: str) -> str:
    """``"High Interest Savings Account"`` → ``"high_interest_savings_account"``."""
    return _NON_ALNUM.sub("_", name.lower()).strip("_") or "account"


def write_account_csvs(transactions: Iterable[Transaction], directory: Path, *, export_date: date) -> list[Path]:
    """Write one ``<account_slug>_<export_date>.csv`` per account and return the paths.

    Accounts keep the order they first appear in, and each file keeps the table's row order.
    Two accounts whose names slug the same get ``_2``, ``_3``… rather than sharing a file."""
    by_account: dict[str, list[Transaction]] = {}
    for transaction in transactions:
        by_account.setdefault(transaction.account, []).append(transaction)

    directory.mkdir(parents=True, exist_ok=True)
    stamp = export_date.isoformat()
    used: set[str] = set()
    paths: list[Path] = []
    for account, rows in by_account.items():
        slug = account_slug(account)
        name, suffix = slug, 2
        while name in used:
            name, suffix = f"{slug}_{suffix}", suffix + 1
        if name != slug:
            logger.warning("Two NBC accounts are both named like %r; writing %r to %s", slug, account, name)
        used.add(name)
        path = directory / f"{name}_{stamp}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            for row in rows:
                writer.writerow([row.date.isoformat(), row.description, row.category, f"{row.amount:.2f}", row.account])
        logger.info("Wrote %d NBC transaction(s) for %s to %s", len(rows), account, path.name)
        paths.append(path)
    return paths


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())
