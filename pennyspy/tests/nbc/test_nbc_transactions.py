"""Offline tests for reading NBC's transaction rows and splitting them into one CSV per account."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pennyspy.scrapers.nbc.transactions import (
    CSV_COLUMNS,
    TransactionParseError,
    account_slug,
    keep_since,
    parse_amount,
    parse_date,
    parse_rows,
    write_account_csvs,
)

TODAY = date(2026, 10, 17)


def _row(when: str, account: str, amount: str, *, debit: bool = False, description: str = "Payment") -> dict:
    return {
        "date": when,
        "description": description,
        "account": account,
        "category": "Transfer",
        "amount": amount,
        "credit": not debit,
        "debit": debit,
    }


ROWS = [
    _row("October 14, 2026", "Chequing Account", "$400.00"),
    _row("October 13, 2026", "High Interest Savings Account", "$5,000.00"),
    _row("October 8, 2026", "Chequing Account", "$12.50", debit=True, description="Fixed monthly fees"),
    _row("September 30, 2026", "Chequing Account", "$1,234.56", debit=True),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("October 14, 2026", date(2026, 10, 14)),
        ("  October  8, 2026 ", date(2026, 10, 8)),
        ("Oct 8, 2026", date(2026, 10, 8)),
        ("2026-10-08", date(2026, 10, 8)),
        ("Today", TODAY),
        ("Yesterday", date(2026, 10, 16)),
    ],
)
def test_parse_date(text: str, expected: date) -> None:
    assert parse_date(text, today=TODAY) == expected


def test_parse_date_rejects_what_it_cannot_read() -> None:
    with pytest.raises(TransactionParseError, match="14 octobre 2026"):
        parse_date("14 octobre 2026", today=TODAY)


@pytest.mark.parametrize(
    ("text", "credit", "debit", "expected"),
    [
        ("$400.00", True, False, Decimal("400.00")),
        ("$5,000.00", True, False, Decimal("5000.00")),
        ("$12.50", False, True, Decimal("-12.50")),
        ("-$12.50", False, True, Decimal("-12.50")),
        ("$0.00", False, True, Decimal("0.00")),
        ("$1 234.00", True, False, Decimal("1234.00")),
        ("-$7.25", False, False, Decimal("-7.25")),
        ("($7.25)", False, False, Decimal("-7.25")),
        ("$7.25", False, False, Decimal("7.25")),
    ],
)
def test_parse_amount(text: str, credit: bool, debit: bool, expected: Decimal) -> None:
    assert parse_amount(text, credit=credit, debit=debit) == expected


def test_parse_amount_rejects_what_it_cannot_read() -> None:
    with pytest.raises(TransactionParseError):
        parse_amount("pending", credit=True, debit=False)


def test_parse_rows_names_the_row_that_failed() -> None:
    with pytest.raises(TransactionParseError, match="Row 2"):
        parse_rows([ROWS[0], _row("soon", "Chequing Account", "$1.00")], today=TODAY)


def test_keep_since_is_inclusive_and_keeps_order() -> None:
    transactions = parse_rows(ROWS, today=TODAY)
    kept = keep_since(transactions, date(2026, 10, 8))
    assert [t.date for t in kept] == [date(2026, 10, 14), date(2026, 10, 13), date(2026, 10, 8)]


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Chequing Account", "chequing_account"),
        ("High Interest Savings Account", "high_interest_savings_account"),
        ("  Mastercard® World Elite  ", "mastercard_world_elite"),
        ("TFSA - Savings", "tfsa_savings"),
        ("***", "account"),
    ],
)
def test_account_slug(name: str, slug: str) -> None:
    assert account_slug(name) == slug


def _read(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def test_one_csv_per_account(tmp_path: Path) -> None:
    transactions = keep_since(parse_rows(ROWS, today=TODAY), date(2026, 10, 1))
    paths = write_account_csvs(transactions, tmp_path, export_date=TODAY)

    assert [p.name for p in paths] == [
        "chequing_account_2026-10-17.csv",
        "high_interest_savings_account_2026-10-17.csv",
    ]
    chequing = _read(paths[0])
    assert chequing[0] == list(CSV_COLUMNS)
    assert chequing[1:] == [
        ["2026-10-14", "Payment", "Transfer", "400.00", "Chequing Account"],
        ["2026-10-08", "Fixed monthly fees", "Transfer", "-12.50", "Chequing Account"],
    ]
    assert _read(paths[1])[1:] == [
        ["2026-10-13", "Payment", "Transfer", "5000.00", "High Interest Savings Account"],
    ]


def test_accounts_that_slug_alike_get_their_own_files(tmp_path: Path) -> None:
    rows = [
        _row("October 14, 2026", "Chequing Account", "$1.00"),
        _row("October 14, 2026", "Chequing-Account", "$2.00"),
    ]
    paths = write_account_csvs(parse_rows(rows, today=TODAY), tmp_path, export_date=TODAY)
    assert [p.name for p in paths] == ["chequing_account_2026-10-17.csv", "chequing_account_2_2026-10-17.csv"]
