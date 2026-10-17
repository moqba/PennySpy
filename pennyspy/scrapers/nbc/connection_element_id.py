"""Locators for National Bank's sign-in pages and online banking.

NBC's front end is React with generated class names (``sc-htoDjs``, ``cfSOAz``), so these key
on ids, ``name``, ``data-test``, ARIA and visible English text. See ``scraping_flow.md``
beside this file for the page each one belongs to.
"""

from enum import StrEnum


class ConsentCss(StrEnum):
    # Didomi's "Respecting your privacy" dialog, on connexion.bnc.ca and app.bnc.ca alike.
    ACCEPT = "button#didomi-notice-agree-button"


class ConnectionElementCss(StrEnum):
    # "You can't access this page from this link", which a direct visit lands on.
    ACCESS_ERROR_TITLE = "h1.error-display__title"
    BACK_TO_SIGN_IN = "button.button__redirection"
    USERNAME = "input#identity"
    PASSWORD = "input#password"
    SUBMIT = "button.login__submit"
    # The code page.
    OTP_FORM = "#multiFactorAuth-common"
    OTP_CHANNEL = "#multiFactorAuth-common h2"
    OTP_DESTINATION = "#multiFactorAuth-common .sending-notice .dsc-alert__title"
    OTP_INPUT = "input#code"
    OTP_SUBMIT = '#multiFactorAuth-common button[type="submit"]'
    OTP_ERROR = "#multiFactorAuth-common .dsc-message--error"


class ConnectionElementXpath(StrEnum):
    # Fallback for the access-error page's button. The code page has a "Back to sign-in" link
    # button too (``back-link-button``), which must never be clicked.
    BACK_TO_SIGN_IN_BY_LABEL = (
        '//button[normalize-space()="Back to sign-in" and not(contains(@class, "back-link-button"))]'
    )
    # Whatever the sign-in form shows when it turns the login down. The exact markup has not
    # been seen yet, so this takes any non-empty alert or error message inside the form, then
    # an error-styled alert anywhere. Only looked for while the password field is still shown.
    LOGIN_ERROR_IN_FORM = (
        '//form[.//input[@type="password"]]//*[(@role="alert" or contains(@class, "dsc-message--error"))'
        ' and normalize-space() != ""]'
    )
    LOGIN_ERROR_ALERT = (
        '//*[contains(@class, "dsc-alert") and (contains(@class, "is-error") or contains(@class, "is-danger"))'
        ' and normalize-space() != ""]'
    )


class AccountsElementXpath(StrEnum):
    SEE_ALL_ACCOUNTS = '//button[normalize-space()="See all my accounts"]'
    TRANSACTION_HISTORY_TAB = (
        '//ul[@id="accounts-tabs__tab-list"]//li[@role="tab" and normalize-space()="Transaction history"]'
    )
    SELECTED_TRANSACTION_HISTORY_TAB = (
        '//ul[@id="accounts-tabs__tab-list"]//li[@role="tab" and @aria-selected="true"'
        ' and normalize-space()="Transaction history"]'
    )
    # Paging has not been seen on the saved page; these are the labels a "load more" control
    # would most likely carry, English first.
    LOAD_MORE = (
        '//*[contains(@class, "transactions-panel")]//button['
        'normalize-space()="Show more" or normalize-space()="Load more" or normalize-space()="See more"'
        ' or normalize-space()="Display more" or normalize-space()="View more"'
        ' or normalize-space()="Show more transactions" or normalize-space()="See more transactions"]'
    )
    SIGN_OUT = '//button[@aria-label="Sign out"]'


class TransactionsCss(StrEnum):
    PANEL = ".transactions-panel"
    PERIOD = "select#filter-form__period"
    APPLY = "button#filter-form__display"
    ROW = ".transactions-panel .transactions-history__row"
    # Anything that looks like a loading indicator inside the transactions panel.
    LOADING = (
        '.transactions-panel [class*="spinner"], .transactions-panel [class*="Spinner"],'
        ' .transactions-panel [class*="loader"], .transactions-panel [aria-busy="true"]'
    )


# The Period dropdown's option for "Last 12 months".
LAST_12_MONTHS = "ONE_YEAR"
