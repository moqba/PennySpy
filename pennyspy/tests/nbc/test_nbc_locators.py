"""Offline tests for the NBC locators, against trimmed copies of NBC's pages.

The pages are cut down to the controls the scraper touches (class names it does not use, SVGs
and all account data dropped; the rows are made up). CSS selectors are checked with
BeautifulSoup's own CSS engine. No XPath engine is installed, so XPath locators are mirrored
in BeautifulSoup, and the mirrors are pinned to the locators they stand in for.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from pennyspy.scrapers.nbc.connection_element_id import (
    LAST_12_MONTHS,
    AccountsElementXpath,
    ConnectionElementCss,
    ConnectionElementXpath,
    ConsentCss,
    TransactionsCss,
)
from pennyspy.scrapers.nbc.nbc import is_signed_in_url

CONSENT = (
    '<div id="didomi-popup"><div role="dialog"><h1>Respecting your privacy</h1>'
    '<div id="buttons">'
    '<button id="didomi-notice-agree-button" class="didomi-components-button" aria-label="Accept">'
    "<span>Accept</span></button>"
    '<button id="didomi-notice-learn-more-button" aria-label="Personalize your preferences">'
    "<span>Personalize</span></button>"
    "</div></div></div>"
)

ACCESS_ERROR = (
    "<main>"
    '<h1 class="title error-display__title">You can\'t access this page from this link</h1>'
    '<button class="bt_cta primary button__redirection" type="button">Back to sign-in</button>'
    "</main>"
)

SIGN_IN = (
    "<form>"
    '<label class="login_label" for="identity">Email ID</label>'
    '<input class="bnc-input sc-htoDjs cfSOAz" id="identity" name="identity" type="email" maxlength="255">'
    '<label class="login_label" for="password">Password</label>'
    '<input class="input-password bnc-input" id="password" name="password" type="password">'
    '<input class="dsc-checkbox__input" type="checkbox" id="rememberMe-check" name="remember">'
    '<button type="submit" class="dsc-btn is-fluid is-primary is-large has-icon login__submit" data-test="button">'
    "<span>lock</span>Sign in</button>"
    "</form>"
)

OTP = (
    '<div id="multiFactorAuth-common" class="connect-form mfa sbip2-mfa">'
    '<h1 class="dsc-heading has-size01">Verification code</h1>'
    '<h2 class="dsc-heading has-size01"><span>A verification code has been sent to you by email to confirm'
    " your identity.</span></h2>"
    '<div class="dsc-alert is-noChildren sending-notice is-information"><div class="dsc-alert__textContainer">'
    '<h4 class="dsc-alert__title"><span>Code sent to <br>j...e@example.com</span></h4></div></div>'
    '<div class="dsc-formGroup has-error mfa-validation-code">'
    '<input placeholder="Enter your code" type="text" maxlength="6" class="dsc-input" id="code" name="code">'
    '<span class="dsc-message dsc-message--error" role="alert">You must enter a verification code.</span>'
    "</div>"
    '<button type="submit" class="dsc-btn is-fluid is-primary">Confirm</button>'
    '<button type="button" class="dsc-link has-icon back-link-button">arrow-left Back to sign-in</button>'
    "</div>"
)

DASHBOARD = (
    '<button type="button" class="dsc-btn is-outlined mc-d-flex"><span class="">See all my accounts</span></button>'
)


def _row(when: str, account: str, amount: str, direction: str) -> str:
    return (
        '<div class="dsc-gridTable__row is-hoverable transactions-history__row" role="row">'
        f'<div class="dsc-gridTable__cell transactions-table__date" role="cell"><p class="dsc-text">{when}</p></div>'
        '<div class="dsc-gridTable__cell transactions-table__description" role="cell">INTERAC e-Transfer</div>'
        f'<div class="dsc-gridTable__cell transactions-table__account" role="cell">{account}</div>'
        '<div class="dsc-gridTable__cell transactions-table__category" role="cell"><p class="dsc-text">Transfer</p>'
        "</div>"
        '<div class="dsc-gridTable__cell transactions-table__amount" role="cell">'
        f'<p class="dsc-text is-paragraph {direction}"><span class="dsc-currencytext">{amount}</span></p></div>'
        "</div>"
    )


ACCOUNTS = (
    '<ul id="accounts-tabs__tab-list" role="tablist">'
    '<li class="tabs-title react-tabs__tab" role="tab" aria-selected="false"><span>Accounts</span></li>'
    '<li class="tabs-title react-tabs__tab react-tabs__tab--selected" role="tab" aria-selected="true">'
    "<span>Transaction history</span></li>"
    '<li class="tabs-title react-tabs__tab" role="tab" aria-selected="false"><span><div>'
    "<span>Upcoming transactions</span></div></span></li>"
    "</ul>"
    '<div class="tabs-component-panel react-tabs__tab-panel accounts-panel" role="tabpanel"></div>'
    '<div class="tabs-component-panel react-tabs__tab-panel transactions-panel react-tabs__tab-panel--selected"'
    ' role="tabpanel">'
    "<form>"
    '<select id="filter-form__period" name="period" class="dsc-select">'
    '<option value="LAST">Last 30 days</option><option value="THREE_MONTHS">Last 3 months</option>'
    '<option value="SIX_MONTHS">Last 6 months</option><option value="ONE_YEAR">Last 12 months</option>'
    '<option value="PERSONALIZED">Personalize dates</option></select>'
    '<button id="filter-form__display" type="submit" class="dsc-btn is-outlined"><span>Apply</span></button>'
    '<button id="filter-form__reset" type="button">Reset</button>'
    "</form>"
    '<div class="dsc-gridTable transactions-table transactions-history__table" role="table">'
    '<div class="dsc-gridTable__rowGroup" role="rowgroup">'
    '<div class="dsc-gridTable__row transactions-history__header-row" role="row">'
    '<div class="dsc-gridTable__cell is-columnheader transactions-table__date" role="columnheader">Date</div>'
    "</div></div>"
    '<div class="dsc-gridTable__rowGroup" role="rowgroup">'
    + _row("October 14, 2026", "Chequing Account", "$400.00", "is-credit")
    + _row("October 8, 2026", "Chequing Account", "$0.00", "is-debit")
    + "</div></div></div>"
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _one(soup: Tag, css: str) -> Tag:
    (match,) = soup.select(css)
    return match


def _text(el: Tag) -> str:
    return " ".join(el.get_text(" ").split())


def test_css_locators_find_their_controls() -> None:
    assert _text(_one(_soup(CONSENT), ConsentCss.ACCEPT)) == "Accept"
    assert _text(_one(_soup(ACCESS_ERROR), ConnectionElementCss.BACK_TO_SIGN_IN)) == "Back to sign-in"
    assert _one(_soup(ACCESS_ERROR), ConnectionElementCss.ACCESS_ERROR_TITLE) is not None

    sign_in = _soup(SIGN_IN)
    assert _one(sign_in, ConnectionElementCss.USERNAME)["name"] == "identity"
    assert _one(sign_in, ConnectionElementCss.PASSWORD)["type"] == "password"
    assert _text(_one(sign_in, ConnectionElementCss.SUBMIT)).endswith("Sign in")

    otp = _soup(OTP)
    assert _one(otp, ConnectionElementCss.OTP_INPUT)["name"] == "code"
    assert _text(_one(otp, ConnectionElementCss.OTP_SUBMIT)) == "Confirm"
    assert "by email" in _text(_one(otp, ConnectionElementCss.OTP_CHANNEL))
    assert _text(_one(otp, ConnectionElementCss.OTP_DESTINATION)) == "Code sent to j...e@example.com"
    assert _one(otp, ConnectionElementCss.OTP_ERROR) is not None


def test_otp_confirm_is_not_the_back_link() -> None:
    (confirm,) = _soup(OTP).select(ConnectionElementCss.OTP_SUBMIT)
    assert "back-link-button" not in confirm.get("class", [])


def test_transaction_locators() -> None:
    page = _soup(ACCOUNTS)
    period = _one(page, TransactionsCss.PERIOD)
    assert LAST_12_MONTHS in [o["value"] for o in period.find_all("option")]
    assert next(o for o in period.find_all("option") if o["value"] == LAST_12_MONTHS).text == "Last 12 months"
    assert _text(_one(page, TransactionsCss.APPLY)) == "Apply"
    rows = page.select(TransactionsCss.ROW)
    assert len(rows) == 2  # the header row is not a transactions-history__row
    first = rows[0]
    assert _text(_one(first, ".transactions-table__account")) == "Chequing Account"
    assert "is-credit" in _one(first, ".transactions-table__amount p")["class"]
    assert "is-debit" in _one(rows[1], ".transactions-table__amount p")["class"]


# ── XPath mirrors ────────────────────────────────────────────────────


def _back_to_sign_in_by_label(soup: BeautifulSoup) -> list:
    # //button[normalize-space()="Back to sign-in" and not(contains(@class, "back-link-button"))]
    return [
        b
        for b in soup.find_all("button")
        if _text(b) == "Back to sign-in" and "back-link-button" not in " ".join(b.get("class", []))
    ]


def _see_all_accounts(soup: BeautifulSoup) -> list:
    # //button[normalize-space()="See all my accounts"]
    return [b for b in soup.find_all("button") if _text(b) == "See all my accounts"]


def _transaction_history_tab(soup: BeautifulSoup, *, selected_only: bool) -> list:
    tab_list = _one(soup, "ul#accounts-tabs__tab-list")
    return [
        li
        for li in tab_list.find_all("li", attrs={"role": "tab"})
        if _text(li) == "Transaction history" and (not selected_only or li.get("aria-selected") == "true")
    ]


def test_the_mirrors_match_the_locators_they_stand_in_for() -> None:
    assert ConnectionElementXpath.BACK_TO_SIGN_IN_BY_LABEL == (
        '//button[normalize-space()="Back to sign-in" and not(contains(@class, "back-link-button"))]'
    )
    assert AccountsElementXpath.SEE_ALL_ACCOUNTS == '//button[normalize-space()="See all my accounts"]'
    assert AccountsElementXpath.TRANSACTION_HISTORY_TAB == (
        '//ul[@id="accounts-tabs__tab-list"]//li[@role="tab" and normalize-space()="Transaction history"]'
    )


def test_back_to_sign_in_label_skips_the_code_pages_back_link() -> None:
    assert len(_back_to_sign_in_by_label(_soup(ACCESS_ERROR))) == 1
    assert _back_to_sign_in_by_label(_soup(OTP)) == []


def test_accounts_xpaths() -> None:
    assert len(_see_all_accounts(_soup(DASHBOARD))) == 1
    page = _soup(ACCOUNTS)
    assert len(_transaction_history_tab(page, selected_only=False)) == 1
    assert len(_transaction_history_tab(page, selected_only=True)) == 1


def test_is_signed_in_url() -> None:
    assert is_signed_in_url("https://app.bnc.ca/dashboard")
    assert is_signed_in_url("https://app.bnc.ca/accounts#main")
    assert not is_signed_in_url("https://app.bnc.ca/auth?code=x")
    assert not is_signed_in_url("https://connexion.bnc.ca/")
