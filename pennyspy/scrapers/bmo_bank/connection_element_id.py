from enum import StrEnum

# BMO's Ionic router keeps previously visited account pages mounted in the DOM (marked
# ``ion-page-hidden`` / ``aria-hidden``). Scoping every transaction/pagination locator to the one
# visible, active tab panel prevents rows and buttons from a hidden account bleeding into the parse.
_ACTIVE_TAB_PANEL = "ion-router-outlet#mainContent > .ion-page:not(.ion-page-hidden) .tab-panel.active-tab-content"

# ── 2FA locator building blocks ──────────────────────────────────────────────────────
# The OTP screens are an Angular micro-frontend that stamps a fresh UUID ``id`` on nearly every
# element it renders, re-tags its components on each build (``_ngcontent-ng-c…``) and has
# reworded its copy. What holds still across builds is the ``name`` on inputs and buttons, the
# ARIA roles, and the visible label text — so each step is located by those, tried in that order,
# and never by an id BMO generates per render.

# XPath 1.0 has no lower-case(); translate() is the portable way to compare text case-blind.
_UPPERCASE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWERCASE = "abcdefghijklmnopqrstuvwxyz"


def _casefold(expression: str) -> str:
    return f"translate({expression}, '{_UPPERCASE}', '{_LOWERCASE}')"


def _contains_any(expression: str, needles: tuple[str, ...]) -> str:
    """XPath predicate body: ``expression``, case-folded, contains any of ``needles``."""
    return " or ".join(f"contains({_casefold(expression)}, '{needle}')" for needle in needles)


# OneTrust keeps its entire consent preference centre — checkboxes, labels, paragraphs of prose —
# hidden in the DOM of every BMO page. Text-matching locators that aren't scoped to the OTP form
# have to step around it, or they match its copy instead of the form we mean to fill in.
_OUTSIDE_COOKIE_BANNER = "not(ancestor-or-self::*[starts-with(@id, 'onetrust') or starts-with(@id, 'ot-')])"

# Wording BMO has used for the "send the code to my phone" delivery option. Matched against both
# the option's label and its radio value, so "SMS text ••• - ••• - 7770" and value "SMS0" both hit.
PHONE_METHOD_WORDS: tuple[str, ...] = ("sms", "text", "phone", "mobile", "cell")
# The confirmation that has to be ticked before BMO will send a code: "…you must confirm you will
# not provide this verification code to anyone."
_DISCLAIMER_WORDS: tuple[str, ...] = (
    "verification code to anyone",
    "will not provide",
    "will not share",
    "keep it private",
)


class ConnectionElementId(StrEnum):
    USERNAME = "//fdc-input[@id='username']/div/div/input"
    PASSWORD = "//fdc-input[@id='password']/div/div/input"
    SIGN_IN = "//button[@name='login-submit']"
    SIGN_IN_ARIA = "//button[@aria-label='Sign in to Online Banking']"
    SIGN_IN_TEXT = "//button[@type='submit'][normalize-space(.)='Sign in']"
    COOKIE_ACCEPT = "//button[@id='onetrust-accept-btn-handler']"
    LOGIN_ERROR_BANNER = "//div[contains(@class,'alert-danger')]"

    # ── 2FA step 1 (the "Let's verify it's you" screen): pick a delivery method, tick the
    # confirmation, SEND CODE. Older builds showed a NEXT interstitial first; current ones land
    # straight on the method list, so NEXT is clicked only when it is actually on the page.
    MFA_NEXT_BUTTON = "//otp-button//button[.//span[contains(@class,'text') and normalize-space(text())='NEXT']]"
    MFA_METHOD_STEP = (
        "//app-select-otp-method"
        " | //*[@role='radiogroup']//input[@type='radio']"
        " | //*[contains(@id,'otp-contact')]//input[@type='radio']"
    )
    # The real radio is styled out of the way behind its label, so the label is the click target;
    # which label is the phone one is decided by its text, then by the radio value it drives.
    MFA_PHONE_RADIO_LABEL = f"//*[@role='radiogroup']//label[{_contains_any('.', PHONE_METHOD_WORDS)}]"
    MFA_PHONE_RADIO_VALUE_LABEL = (
        f"//label[@for=//input[@type='radio'][{_contains_any('@value', PHONE_METHOD_WORDS)}]/@id]"
    )
    MFA_PHONE_RADIO_LABEL_ANYWHERE = (
        f"//label[@for][{_OUTSIDE_COOKIE_BANNER}][{_contains_any('.', PHONE_METHOD_WORDS)}]"
    )
    MFA_PHONE_RADIO_VALUE = f"//input[@type='radio'][{_contains_any('@value', PHONE_METHOD_WORDS)}]"
    # Last resort when nothing identifies the phone option: the first option BMO offers.
    MFA_CONTACT_RADIO_LABEL = "//*[@role='radiogroup']//label[@for]"
    MFA_CONTACT_RADIO = "//*[@role='radiogroup']//input[@type='radio']"

    MFA_AGREE_CHECKBOX_LABEL = "//label[.//input[@type='checkbox'][contains(@name,'proceed')]]"
    MFA_AGREE_CHECKBOX_LABEL_BY_ID = "//*[contains(@id,'proceed')]//label[.//input[@type='checkbox']]"
    MFA_AGREE_CHECKBOX_LABEL_BY_TEXT = (
        f"//label[{_OUTSIDE_COOKIE_BANNER}][.//input[@type='checkbox']][{_contains_any('.', _DISCLAIMER_WORDS)}]"
    )
    # Checked state is read as a live DOM property through this CSS selector: the design system
    # never writes ``checked`` back into the markup, so the HTML attribute stays absent.
    MFA_AGREE_CHECKBOX_INPUT = "input[type='checkbox'][name*='proceed'], [id*='proceed'] input[type='checkbox']"

    MFA_SEND_CODE = "//button[@name='login-step1-confirm-button']"
    MFA_SEND_CODE_BY_NAME = "//button[contains(@name,'step1-confirm')]"
    # starts-with, not contains: step 2's "RESEND CODE" button contains "send code" too, and a
    # stray resend is a second text message to the user.
    MFA_SEND_CODE_BY_TEXT = (
        f"//button[{_OUTSIDE_COOKIE_BANNER}]"
        f"[starts-with(normalize-space({_casefold('.')}), 'send')]"
        f"[contains({_casefold('.')}, 'code')]"
    )

    # ── 2FA step 2 (the "Enter your code" screen): type the OTP, leave the device untrusted,
    # CONFIRM. The input's id is a UUID, so it is reached through its wrapper, name or type.
    OTP_INPUT = "//fdc-input[@id='otpCode']//input"
    OTP_INPUT_BY_NAME = f"//input[contains({_casefold('@name')}, 'otpcode')]"
    OTP_INPUT_BY_ID = f"//input[contains({_casefold('@id')}, 'otpcode')]"
    OTP_INPUT_BY_FORM_CONTROL = f"//*[contains({_casefold('@formcontrolname')}, 'otpcode')]//input"
    OTP_INPUT_NUMERIC = "//app-enter-otp-code//input[@inputmode='numeric' or @type='number']"
    OTP_STEP = (
        f"//app-enter-otp-code | //fdc-input[@id='otpCode']//input | //input[contains({_casefold('@name')}, 'otpcode')]"
    )
    OTP_ERROR = f"//*[{_OUTSIDE_COOKIE_BANNER}][contains(@class,'error-message')]"

    # Never clicked — only read, so a build that arrives with "Trust this device" pre-ticked is
    # caught and unticked instead of quietly registering the machine with BMO.
    MFA_TRUST_DEVICE_INPUT = "input[type='checkbox'][name*='trust'], [id*='trust-this-device'] input[type='checkbox']"
    MFA_TRUST_DEVICE_LABEL = "//label[.//input[@type='checkbox'][contains(@name,'trust')]]"

    MFA_CONFIRM = "//button[@name='login-step2-confirm-button']"
    MFA_CONFIRM_BY_NAME = "//button[contains(@name,'step2-confirm')]"
    MFA_CONFIRM_BY_TEXT = f"//button[{_OUTSIDE_COOKIE_BANNER}][normalize-space({_casefold('.')})='confirm']"
    MFA_CONTINUE = "//button[.//span[normalize-space()='CONTINUE']]"
    MFA_CONTINUE_BY_TEXT = f"//button[{_OUTSIDE_COOKIE_BANNER}][normalize-space({_casefold('.')})='continue']"

    # Account routing: the side nav lists every account with a /account-details/{ba,cc}/{uuid} href,
    # so one loaded account-details page yields the type (bank vs credit card) of all of them.
    ACTIVE_TAB_PANEL = _ACTIVE_TAB_PANEL
    SIDE_NAV_LINK = "a.side-nav__link"
    # Web-parsed transaction table. TRANSACTION_ROWS / TRANSACTION_HEADERS are *relative* XPaths,
    # evaluated with the active tab panel as the context node (see _extract_table_from_page).
    TRANSACTION_ROWS = ".//table//tbody/tr[contains(@class, 'table-row')]"
    TRANSACTION_HEADERS = ".//table//th"
    TRANSACTION_ROW_INTERACTIVE = f"{_ACTIVE_TAB_PANEL} tr.table-row.table-row-interactive"
    PAGINATION_NEXT_BUTTON = f"{_ACTIVE_TAB_PANEL} button[data-pagination-btn='true'].next-button"
    PAGINATION_RANGE_LABEL = f"{_ACTIVE_TAB_PANEL} .pagination-range-label"
    ROWS_PER_PAGE_SELECT = f"{_ACTIVE_TAB_PANEL} select[id^='rows-per-page-']"
