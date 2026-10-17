from enum import StrEnum


class ConnectionElementXpath(StrEnum):
    # WS's October 2026 login page made the email field type="email" and dropped its aria-label,
    # the password field's inputmode and the submit button's role="button". These key only on
    # what both the old and the new page carry.
    USERNAME = '//input[(@type="email" or @type="text") and @inputmode="email"]'
    PASSWORD = '//input[@type="password"]'
    SUBMIT = '//button[@type="submit"]'
    USER_INCORRECT = '//p[contains(text(), "Your email or password was incorrect")]'
    # The October 2026 SMS code page dropped the "– – – – – –" placeholder; the field now carries
    # data-testid="otp-input". autocomplete="one-time-code" is on both the old and the new one.
    PHONE_2FA = '//input[@data-testid="otp-input" or (@type="text" and @autocomplete="one-time-code")]'
    FAILED_2FA = '//div[@role="alert" and contains(text(), "Try again or get a new code")]'
    # No role="button" any more: the October 2026 pages dropped it from their buttons.
    PASSKEY_MAYBE_LATER = '//button[.//span[normalize-space()="Maybe later"]]'
    # "Use your passkey to verify that it's you", shown once Chrome's passkey request for an
    # account with a passkey fails. "Try another way" is what sends the SMS code. The data-testid
    # goes first; the visible label is the fallback for when WS renames it.
    PASSKEY_TRY_ANOTHER_WAY = '//button[@data-testid="passkey-challenge-2fa-error-try-another-way-button"]'
    PASSKEY_TRY_ANOTHER_WAY_BY_LABEL = '//button[.//span[normalize-space()="Try another way"]]'
    # "Check your trusted device": WS sent a push to the Wealthsimple app on the user's phone and
    # waits for them to tap Yes. It can follow the password or the SMS code. The page also has a
    # "Try another way" button, which the passkey label locator above would match, so this one
    # must be checked before it. The heading is the only stable thing on the page.
    TRUSTED_DEVICE = '//h1[normalize-space()="Check your trusted device"]'


class ActivityElementXpath(StrEnum):
    # The redesigned feed dropped the redundant role="button" this used to also require, so
    # the visible label is all that is matched on.
    LOAD_MORE = '//button[.//span[normalize-space()="Load more"]]'


class AccountGraphXpath(StrEnum):
    """The chart toolbar on the account-details page.

    Both controls the toolbar offers — the metric ("Account value" / "Returns") and the time
    range (1D … ALL) — are ``role="tab"`` buttons inside a ``role="tablist"``, and their label
    is the only stable thing about them: the class names are styled-components hashes, and the
    one ``data-qa`` on the toolbar spells out the *current* selection
    (``time-filter-segmented-control_value-1y``) rather than naming the control.
    """

    # ``{label}`` is a tab's visible text, e.g. "Account value" or "1Y".
    CHART_TAB = '//div[@role="tablist"]//button[@role="tab" and .//span[normalize-space()="{label}"]]'


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
    """The export dialog's period dropdown and "All accounts" master checkbox.

    The master checkbox is the only checkbox row without an account ``data-testid``, so it
    can only be matched on its label. WS moved the label out of the control in the September
    2026 redesign -- the row now reads "Accounts" / "All accounts" with the checkbox beside
    it rather than wrapping it -- so it is looked for through a chain, most- to
    least-specific, and treated as optional: ticking the per-account rows reaches the same
    place (see ``Wealthsimple._select_all_export_accounts``).
    """

    # The control wraps its own label. WS's pre-redesign shape, kept first for older builds.
    ALL_ACCOUNTS_CHECKBOX = '//button[@role="checkbox" and .//p[normalize-space()="All accounts"]]'
    # The control is named by ARIA rather than by a label it contains.
    ALL_ACCOUNTS_BY_ARIA = '//*[(@role="checkbox" or @type="checkbox") and @aria-label="All accounts"]'
    # The label and the control are siblings in one row: find the label, then the checkbox
    # sharing its row. Two ancestor levels covers a label wrapped in its own cell.
    ALL_ACCOUNTS_IN_ROW = (
        '//*[normalize-space(text())="All accounts"]/ancestor::*[self::div or self::li][2]'
        '//*[@role="checkbox" or @type="checkbox"]'
    )
    # ``{label}`` is one of the ExportPeriod values. The role filter keeps the match off the
    # selector button's own text, which renders the current selection in a plain <p>.
    PERIOD_OPTION = '//*[(@role="option" or @role="menuitem" or self::li) and normalize-space()="{label}"]'
    PERIOD_OPTION_IN_LISTBOX = '//*[@role="listbox"]//*[normalize-space()="{label}"]'
