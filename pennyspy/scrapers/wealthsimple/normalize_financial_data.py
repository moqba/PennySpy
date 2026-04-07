import re

import pandas as pd

INTERNAL_ACCOUNTS = {"Chequing • Main", "TFSA", "Non-registered (Managed)", ""}

TRADE_TYPES = frozenset(
    {
        "Limit buy",
        "Limit sell",
        "Market buy",
        "Market sell",
        "Fractional buy",
        "Fractional sell",
    }
)

BUY_TYPES = frozenset({"Limit buy", "Market buy", "Fractional buy"})
SELL_TYPES = frozenset({"Limit sell", "Market sell", "Fractional sell"})

TRANSFER_TYPES = frozenset(
    {
        "Interac e-Transfer",
        "Electronic funds transfer",
        "Direct deposit",
        "Transfer",
        "Withdrawal",
        "Recurring deposit",
    }
)


def normalize_financial_df(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for _, row in df.iterrows():
        tx_type = str(row.get("Type", "") or "")
        result = _normalize_row(row, tx_type)
        if result:
            rows.append(result)

    result_df = pd.DataFrame(rows, columns=["Date", "Payee", "Account", "Notes", "Amount"])
    result_df["Date"] = pd.to_datetime(result_df["Date"], errors="coerce")
    result_df = result_df.dropna(subset=["Date"]).sort_values("Date", ascending=False).reset_index(drop=True)
    return result_df


def _normalize_row(row: pd.Series, tx_type: str) -> dict | None:
    if tx_type in TRADE_TYPES:
        return _normalize_trade(row, tx_type)
    if tx_type == "Dividend":
        return _normalize_dividend(row)
    if tx_type == "Sold asset":
        return _normalize_sold_asset(row)
    if tx_type in TRANSFER_TYPES:
        return _normalize_transfer(row, tx_type)
    return _normalize_simple(row, tx_type)


def _normalize_trade(row: pd.Series, tx_type: str) -> dict | None:
    date = _get(row, "Filled") or _get(row, "Submitted")
    if not date:
        return None

    ticker = _get(row, "Ticker")
    account = _get(row, "Account")

    qty = _get(row, "Entered quantity") or _get(row, "Filled quantity")
    limit = _get(row, "Limit price")
    if limit:
        notes = f"{tx_type} {qty} @ {limit}".strip()
    else:
        notes = f"{tx_type} {qty}".strip()

    if tx_type in BUY_TYPES:
        raw = _get(row, "Total cost") or _get(row, "Estimated total cost") or _get(row, "Button amount")
        amount = _negate(parse_amount(raw))
    else:
        # Sell: amount from button header (region has no total value field)
        raw = _get(row, "Button amount") or _get(row, "Total cost") or _get(row, "Total")
        amount = parse_amount(raw)

    return {"Date": date, "Payee": ticker, "Account": account, "Notes": notes, "Amount": amount}


def _normalize_dividend(row: pd.Series) -> dict | None:
    # Completed dividends: have Date + Amount fields
    # Upcoming/recent dividends: have Source + Net dividend amount fields (no Date yet)
    date = _get(row, "Date")
    source = _get(row, "Source") or _get(row, "Ticker")
    account = _get(row, "Account")
    raw = _get(row, "Net dividend amount") or _get(row, "Amount") or _get(row, "Button amount")
    amount = parse_amount(raw)
    if amount is None or raw.strip() == "Pending":
        return None
    return {"Date": date, "Payee": source, "Account": account, "Notes": "Dividend", "Amount": amount}


def _normalize_sold_asset(row: pd.Series) -> dict | None:
    # Sold asset (robo-managed): all data comes from button header; no region fields beyond Ticker
    # No date available — skip these in the time-series output
    date = _get(row, "Date") or _get(row, "Filled") or _get(row, "Submitted")
    if not date:
        return None
    ticker = _get(row, "Ticker")
    account = _get(row, "Account")
    amount = parse_amount(_get(row, "Button amount") or _get(row, "Total cost"))
    return {"Date": date, "Payee": ticker, "Account": account, "Notes": "Sold asset", "Amount": amount}


def _normalize_transfer(row: pd.Series, tx_type: str) -> dict | None:
    date = _get(row, "Date")
    if not date:
        return None

    from_ = _get(row, "From")
    to = _get(row, "To")
    payee = ""
    if from_ and from_ not in INTERNAL_ACCOUNTS:
        payee = from_
    elif to and to not in INTERNAL_ACCOUNTS:
        payee = to

    account = _get(row, "Account")
    if not account:
        if to in INTERNAL_ACCOUNTS - {""}:
            account = to
        elif from_ in INTERNAL_ACCOUNTS - {""}:
            account = from_

    amount = parse_amount(_get(row, "Amount"))
    return {"Date": date, "Payee": payee, "Account": account, "Notes": tx_type, "Amount": amount}


def _normalize_simple(row: pd.Series, tx_type: str) -> dict | None:
    date = _get(row, "Date")
    if not date:
        return None

    payee = _get(row, "Button payee")
    if not payee:
        from_ = _get(row, "From")
        to = _get(row, "To")
        if from_ and from_ not in INTERNAL_ACCOUNTS:
            payee = from_
        elif to and to not in INTERNAL_ACCOUNTS:
            payee = to

    account = _get(row, "Account")
    raw = _get(row, "Amount") or _get(row, "Total")
    amount = parse_amount(raw)
    notes = tx_type
    return {"Date": date, "Payee": payee, "Account": account, "Notes": notes, "Amount": amount}


def _get(row: pd.Series, key: str) -> str:
    val = row.get(key, "")
    if pd.isna(val):
        return ""
    s = str(val).strip()
    return s if s not in ("nan", "None") else ""


def _negate(value: float | None) -> float | None:
    if value is None:
        return None
    return -abs(value)


def parse_amount(raw: str) -> float | None:
    """Convert messy currency strings like '− $1,512.00 CAD' to signed floats."""
    if not raw or raw.strip() in ("", "nan"):
        return None
    negative = "\u2212" in raw or "-" in raw
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits:
        return None
    value = float(digits)
    return -value if negative else value
