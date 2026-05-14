from pathlib import Path

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from pennyspy.scrapers.wealthsimple.activity_fields import ActivityField
from pennyspy.scrapers.wealthsimple.normalize_financial_data import normalize_financial_df
from pennyspy.scrapers.wealthsimple.wealthsimple import build_activity_row

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "tfsa_payment.html",
        "card_payment.html",
        "credit_card_purchase.html",
    ],
)
def test_parses_fixture_to_expected_row(fixture_name: str):
    html = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    button = soup.find("button", id=lambda i: bool(i) and i.endswith("-header"))
    region = soup.find("div", attrs={"role": "region"})
    assert button is not None, f"button not found in {fixture_name}"
    assert region is not None, f"region not found in {fixture_name}"

    row = build_activity_row(button.decode_contents(), region.decode_contents())

    assert row is not None
    df = pd.DataFrame([row], columns=[f.value for f in ActivityField])
    normalized = normalize_financial_df(df)
    assert not normalized.empty


def test_card_payment_account_defaults_to_from():
    html = (FIXTURES / "card_payment.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    button = soup.find("button", id=lambda i: bool(i) and i.endswith("-header"))
    region = soup.find("div", attrs={"role": "region"})
    row = build_activity_row(button.decode_contents(), region.decode_contents())
    df = pd.DataFrame([row], columns=[f.value for f in ActivityField])
    normalized = normalize_financial_df(df)
    assert normalized["Account"].iloc[0] == "Chequing • Main"


def test_credit_card_purchase_extracts_negative_amount():
    html = (FIXTURES / "credit_card_purchase.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    button = soup.find("button", id=lambda i: bool(i) and i.endswith("-header"))
    region = soup.find("div", attrs={"role": "region"})
    row = build_activity_row(button.decode_contents(), region.decode_contents())
    df = pd.DataFrame([row], columns=[f.value for f in ActivityField])
    normalized = normalize_financial_df(df)
    assert normalized["Amount"].iloc[0] == pytest.approx(-131.60)
    assert normalized["Payee"].iloc[0] == "Amzn Mktp Ca"
    assert normalized["Account"].iloc[0] == "Credit card • Wealthsimple credit card"


def test_amount_falls_back_to_button_amount_when_region_total_missing():
    """Region captured mid-render may lack the Total field; button header always has it."""
    row = {
        "Date": "May 14, 2026",
        "Account": "Credit card • Wealthsimple credit card",
        "Type": "Purchase",
        "Button payee": "Amzn Mktp Ca",
        "Button amount": "− $131.60 CAD",
    }
    df = pd.DataFrame([row], columns=[f.value for f in ActivityField])
    normalized = normalize_financial_df(df)
    assert normalized["Amount"].iloc[0] == pytest.approx(-131.60)
