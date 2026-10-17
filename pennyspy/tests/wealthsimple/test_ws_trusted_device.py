"""Offline tests for Wealthsimple's "Check your trusted device" page.

WS shows it after the password or after the SMS code when the login comes from a new place: it
sends a push to the Wealthsimple app and waits for the user to tap Yes. The page has a "Try
another way" button of its own, which the passkey challenge's label locator also matches, so the
page must be recognised before that locator is tried. The XPath conditions are mirrored in
BeautifulSoup against a copy of the page (no XPath engine is installed, and no browser is
launched).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from pennyspy.scrapers.base import AuthStep
from pennyspy.scrapers.wealthsimple.connection_element_id import ConnectionElementXpath as Xpath
from pennyspy.scrapers.wealthsimple.wealthsimple import _POST_LOGIN_LOCATORS, TRUSTED_DEVICE_MESSAGE, Wealthsimple

# The page as WS serves it in October 2026, trimmed to its content (class names and the logo dropped).
TRUSTED_DEVICE = (
    '<div id="main" role="main">'
    '<img width="375" height="260" alt="Trusted device login approval image">'
    "<h1>Check your trusted device</h1>"
    "<p>We noticed you’re logging in from a new place, so we sent a notification to your trusted device."
    " Open the Wealthsimple app and tap <span>Yes</span> on the prompt to verify it’s you.</p>"
    '<button type="button"><span>Resend notification</span><span role="status"></span></button>'
    '<button type="button"><span>Try another way</span><span role="status"></span></button>'
    "</div>"
)


def _trusted_device(soup: BeautifulSoup) -> list:
    # //h1[normalize-space()="Check your trusted device"]
    return [h for h in soup.find_all("h1") if " ".join(h.get_text().split()) == "Check your trusted device"]


def _try_another_way_by_label(soup: BeautifulSoup) -> list:
    # //button[.//span[normalize-space()="Try another way"]]
    return [
        b
        for b in soup.find_all("button")
        if any(" ".join(s.get_text().split()) == "Try another way" for s in b.find_all("span"))
    ]


def test_the_mirror_matches_the_locator() -> None:
    assert Xpath.TRUSTED_DEVICE == '//h1[normalize-space()="Check your trusted device"]'


def test_the_locator_finds_the_page() -> None:
    assert len(_trusted_device(BeautifulSoup(TRUSTED_DEVICE, "html.parser"))) == 1


def test_the_page_is_recognised_before_the_passkey_label_fallback() -> None:
    # The passkey challenge's label locator would click this page's "Try another way" too.
    assert _try_another_way_by_label(BeautifulSoup(TRUSTED_DEVICE, "html.parser"))
    order = [locator for _by, locator in _POST_LOGIN_LOCATORS]
    assert order.index(Xpath.TRUSTED_DEVICE) < order.index(Xpath.PASSKEY_TRY_ANOTHER_WAY_BY_LABEL)
    assert order.index(Xpath.PHONE_2FA) < order.index(Xpath.TRUSTED_DEVICE)


def test_the_approval_step_tells_the_user_to_tap_yes() -> None:
    scraper = Wealthsimple.__new__(Wealthsimple)  # no browser
    scraper._awaiting_device_approval = False
    step = scraper._awaiting_approval_step()
    assert step == AuthStep(status="waiting_for_external", message=TRUSTED_DEVICE_MESSAGE)
    assert scraper._awaiting_device_approval
    assert "tap Yes" in step.message
