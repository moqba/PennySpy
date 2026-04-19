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
