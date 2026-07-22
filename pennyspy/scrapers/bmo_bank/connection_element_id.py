from enum import StrEnum

# BMO's Ionic router keeps previously visited account pages mounted in the DOM (marked
# ``ion-page-hidden`` / ``aria-hidden``). Scoping every transaction/pagination locator to the one
# visible, active tab panel prevents rows and buttons from a hidden account bleeding into the parse.
_ACTIVE_TAB_PANEL = (
    "ion-router-outlet#mainContent > .ion-page:not(.ion-page-hidden) "
    ".tab-panel.active-tab-content"
)


class ConnectionElementId(StrEnum):
    USERNAME = "//fdc-input[@id='username']/div/div/input"
    PASSWORD = "//fdc-input[@id='password']/div/div/input"
    SIGN_IN = "//button[@aria-label='Sign in to Online Banking']"
    COOKIE_ACCEPT = "//button[@id='onetrust-accept-btn-handler']"
    LOGIN_ERROR_BANNER = "//div[contains(@class,'alert-danger')]"
    # 2FA flow
    MFA_NEXT_BUTTON = "//otp-button//button[.//span[contains(@class,'text') and normalize-space(text())='NEXT']]"
    MFA_PHONE_RADIO = "//label[span[text()='SMS text']]"
    MFA_AGREE_CHECKBOX = "//label[contains(@class,'checkbox-label')]//input[@type='checkbox']"
    MFA_SEND_CODE = "//otp-button//button[.//span[contains(@class,'text') and normalize-space(text())='SEND CODE']]"
    OTP_INPUT = "//input[@id='otp-input']"
    MFA_CONFIRM = "//button[.//span[normalize-space()='CONFIRM']]"
    MFA_CONTINUE = "//button[.//span[normalize-space()='CONTINUE']]"
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
