"""Offline tests for the Wealthsimple passkey-challenge locators.

For an account with a passkey, WS asks for it right after the password. Once Chrome's passkey
request times out, WS shows "Use your passkey to verify that it's you", and the SMS code is only
sent after "Try another way" is clicked. Each XPath's condition is mirrored in BeautifulSoup
against a copy of that page (no XPath engine is installed, and no browser is launched).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from pennyspy.scrapers.wealthsimple.connection_element_id import ConnectionElementXpath as Xpath
from pennyspy.scrapers.wealthsimple.wealthsimple import _POST_LOGIN_LOCATORS

# The challenge as WS serves it today, trimmed to the controls (class names and the image dropped).
CHALLENGE = (
    '<div id="main" role="main">'
    "<h1>Use your passkey to verify that it’s you</h1>"
    '<p id="passkey-challenge-2fa-error-description">'
    "Complete login with your face, fingerprint, or passcode.</p>"
    '<button type="button" data-testid="passkey-challenge-2fa-error-try-another-way-button">'
    '<span>Try another way</span><span role="status"></span></button>'
    '<button type="button" data-testid="passkey-challenge-2fa-error-retry-button">'
    '<span>Verify with passkey</span><span role="status"></span></button>'
    "</div>"
)


def _by_testid(soup: BeautifulSoup) -> list:
    # //button[@data-testid="passkey-challenge-2fa-error-try-another-way-button"]
    return soup.find_all("button", attrs={"data-testid": "passkey-challenge-2fa-error-try-another-way-button"})


def _by_label(soup: BeautifulSoup) -> list:
    # //button[.//span[normalize-space()="Try another way"]]
    return [
        b
        for b in soup.find_all("button")
        if any(" ".join(s.get_text().split()) == "Try another way" for s in b.find_all("span"))
    ]


def test_the_mirrors_match_the_locators_they_stand_in_for() -> None:
    assert Xpath.PASSKEY_TRY_ANOTHER_WAY == (
        '//button[@data-testid="passkey-challenge-2fa-error-try-another-way-button"]'
    )
    assert Xpath.PASSKEY_TRY_ANOTHER_WAY_BY_LABEL == '//button[.//span[normalize-space()="Try another way"]]'


def test_both_locators_find_try_another_way_not_the_retry_button() -> None:
    soup = BeautifulSoup(CHALLENGE, "html.parser")
    for finder in (_by_testid, _by_label):
        (button,) = finder(soup)
        assert button["data-testid"].endswith("try-another-way-button")


def test_the_otp_field_is_looked_for_first() -> None:
    # A page already showing the SMS code field must never be treated as the challenge.
    assert [locator for _by, locator in _POST_LOGIN_LOCATORS] == [
        Xpath.PHONE_2FA,
        Xpath.PASSKEY_TRY_ANOTHER_WAY,
        Xpath.PASSKEY_TRY_ANOTHER_WAY_BY_LABEL,
    ]
