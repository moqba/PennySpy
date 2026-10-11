"""Offline tests for the Wealthsimple login-form locators.

WS's October 2026 login page made the email field ``type="email"`` and dropped the aria-label,
the password field's ``inputmode`` and the submit button's ``role="button"`` that the old
locators required, so login failed before typing anything. The locators now key only on what
both the old and the new form carry. Each XPath's condition is mirrored in BeautifulSoup against
a copy of both forms (no XPath engine is installed, and no browser is launched).
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from pennyspy.scrapers.wealthsimple.connection_element_id import ConnectionElementXpath as Xpath

# The form as WS serves it today, trimmed to the controls (class names and the SVG icon dropped).
CURRENT_FORM = (
    '<form novalidate="">'
    '<label for="_r_2_">Email</label>'
    '<input id="_r_2_" required="" inputmode="email" autocomplete="username webauthn"'
    ' data-qa="login-email" type="email" value="" name="email">'
    '<label for="_r_5_">Password</label>'
    '<input id="_r_5_" required="" autocomplete="current-password" data-qa="login-password"'
    ' type="password" value="" name="password">'
    '<button aria-label="Show password" title="Show password" type="button" aria-pressed="false">'
    "</button>"
    '<button type="submit" data-testid="login-form-submit-ftux"><span>Log in</span></button>'
    '<button type="button" data-testid="login-form-passkey-submit"><span>Log in with a passkey</span>'
    "</button>"
    '<div inert=""><input type="hidden" name="cf-turnstile-response"></div>'
    "</form>"
)

# The form the old locators were written against.
PREVIOUS_FORM = (
    "<form>"
    '<input type="text" inputmode="email" aria-label="Log in email">'
    '<input type="password" inputmode="text">'
    '<button type="submit" role="button">Log in</button>'
    "</form>"
)


def _username(soup: BeautifulSoup) -> list:
    # //input[(@type="email" or @type="text") and @inputmode="email"]
    return [i for i in soup.find_all("input") if i.get("type") in ("email", "text") and i.get("inputmode") == "email"]


def _password(soup: BeautifulSoup) -> list:
    # //input[@type="password"]
    return soup.find_all("input", attrs={"type": "password"})


def _submit(soup: BeautifulSoup) -> list:
    # //button[@type="submit"]
    return soup.find_all("button", attrs={"type": "submit"})


def test_the_mirrors_match_the_locators_they_stand_in_for() -> None:
    assert Xpath.USERNAME == '//input[(@type="email" or @type="text") and @inputmode="email"]'
    assert Xpath.PASSWORD == '//input[@type="password"]'
    assert Xpath.SUBMIT == '//button[@type="submit"]'


@pytest.mark.parametrize("markup", [CURRENT_FORM, PREVIOUS_FORM], ids=["current", "previous"])
def test_each_locator_finds_exactly_one_control(markup: str) -> None:
    soup = BeautifulSoup(markup, "html.parser")
    assert len(_username(soup)) == 1
    assert len(_password(soup)) == 1
    assert len(_submit(soup)) == 1


def test_submit_is_the_log_in_button_not_the_passkey_one() -> None:
    (button,) = _submit(BeautifulSoup(CURRENT_FORM, "html.parser"))
    assert button.get_text(strip=True) == "Log in"
