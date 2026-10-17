"""Offline tests for the Wealthsimple passkey-challenge locators.

For an account with a passkey, WS asks for it right after the password. Once Chrome's passkey
request times out, WS shows "Use your passkey to verify that it's you", and the SMS code is only
sent after "Try another way" is clicked. Each XPath's condition is mirrored in BeautifulSoup
against a copy of that page (no XPath engine is installed, and no browser is launched).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from pennyspy.scrapers.wealthsimple.connection_element_id import ConnectionElementXpath as Xpath
from pennyspy.scrapers.wealthsimple.wealthsimple import _POST_LOGIN_LOCATORS, is_signed_in_url

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
        Xpath.TRUSTED_DEVICE,
        Xpath.PASSKEY_TRY_ANOTHER_WAY,
        Xpath.PASSKEY_TRY_ANOTHER_WAY_BY_LABEL,
    ]


# The SMS code field as WS serves it since October 2026 (no placeholder), and as it was before.
OTP_FIELD = (
    '<input id="_r_b_" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" '
    'data-testid="otp-input" type="text" value="" name="one-time-code">'
    '<input type="hidden" name="cf-turnstile-response" value="">'
)
OLD_OTP_FIELD = '<input type="text" autocomplete="one-time-code" placeholder="– – – – – –">'


def _otp_fields(soup: BeautifulSoup) -> list:
    # //input[@data-testid="otp-input" or (@type="text" and @autocomplete="one-time-code")]
    return [
        i
        for i in soup.find_all("input")
        if i.get("data-testid") == "otp-input" or (i.get("type") == "text" and i.get("autocomplete") == "one-time-code")
    ]


def test_the_otp_mirror_matches_the_locator() -> None:
    assert Xpath.PHONE_2FA == '//input[@data-testid="otp-input" or (@type="text" and @autocomplete="one-time-code")]'


def test_the_otp_locator_finds_the_field_with_and_without_a_placeholder() -> None:
    for page in (OTP_FIELD, OLD_OTP_FIELD):
        (field,) = _otp_fields(BeautifulSoup(page, "html.parser"))
        assert field["autocomplete"] == "one-time-code"


def test_the_otp_locator_ignores_the_challenge_page() -> None:
    assert _otp_fields(BeautifulSoup(CHALLENGE, "html.parser")) == []


def test_the_login_pages_under_app_are_not_signed_in() -> None:
    # /login redirects to /app/login, which also hosts the passkey challenge and the code page.
    for url in (
        "https://my.wealthsimple.com/app/login",
        "https://my.wealthsimple.com/app/login?redirect=%2Fapp%2Fhome",
        "https://my.wealthsimple.com/app/login/",
        "https://my.wealthsimple.com/login",
        "https://my.wealthsimple.com/app/verify",
    ):
        assert not is_signed_in_url(url), url


def test_the_app_pages_are_signed_in() -> None:
    for url in (
        "https://my.wealthsimple.com/app/home",
        "https://my.wealthsimple.com/app/home?tab=1",
        "https://my.wealthsimple.com/app/activity",
        "https://my.wealthsimple.com/app/account-details/tfsa-abc",
    ):
        assert is_signed_in_url(url), url
