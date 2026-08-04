from enum import StrEnum


class ConnectionElementXpath(StrEnum):
    USERNAME = '//input[@type="text" and @inputmode="email" and @aria-label="Log in email"]'
    PASSWORD = '//input[@type="password" and @inputmode="text"]'
    SUBMIT = '//button[@type="submit" and @role="button"]'
    USER_INCORRECT = '//p[contains(text(), "Your email or password was incorrect")]'
    PHONE_2FA = '//input[@type="text" and @autocomplete="one-time-code" and @placeholder="– – – – – –"]'
    FAILED_2FA = '//div[@role="alert" and contains(text(), "Try again or get a new code")]'
    PASSKEY_MAYBE_LATER = '//button[@role="button" and .//span[normalize-space()="Maybe later"]]'


class ActivityElementXpath(StrEnum):
    LOAD_MORE = '//button[@role="button" and .//span[normalize-space()="Load more"]]'


class ExportElementCss(StrEnum):
    """The activity-page "Download activities" export dialog.

    Every step is addressed by ``data-testid``: the surrounding class names are
    styled-components hashes that WS regenerates on each front-end deploy.
    """

    DOWNLOAD_ACTIVITIES = 'button[data-testid="button-download-activities"]'
    PERIOD_SELECTOR = 'button[data-testid="activities-export-period-selector"]'
    NEXT = 'button[data-testid="button-activities-export-next"]'
    # Only ``button`` rows are selectable — an account still being opened renders as a
    # non-interactive ``div`` carrying the same data-testid, and must not block the flow.
    ACCOUNT_ROW = 'button[data-testid="generate-documents-account-row"]'
    DOWNLOAD_CSV = 'button[data-testid="generate-documents-cta-button"]'


class ExportElementXpath(StrEnum):
    # The "All accounts" master checkbox is the only checkbox row without an account
    # data-testid, so match it on its label instead.
    ALL_ACCOUNTS_CHECKBOX = '//button[@role="checkbox" and .//p[normalize-space()="All accounts"]]'
    # ``{label}`` is one of the ExportPeriod values. The role filter keeps the match off the
    # selector button's own text, which renders the current selection in a plain <p>.
    PERIOD_OPTION = '//*[(@role="option" or @role="menuitem" or self::li) and normalize-space()="{label}"]'
    PERIOD_OPTION_IN_LISTBOX = '//*[@role="listbox"]//*[normalize-space()="{label}"]'
