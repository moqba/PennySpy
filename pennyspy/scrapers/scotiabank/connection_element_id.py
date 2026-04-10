from enum import StrEnum


class ConnectionElementId(StrEnum):
    # Login form
    USERNAME = "//input[@id='usernameInput-input']"
    PASSWORD = "//input[@id='password-input']"
    SIGN_IN = "//button[@id='signIn']"
    # Wrong credentials indicator
    LOGIN_ERROR = "//div[@id='globalError']"
    # 2SV trust device page
    TRUST_DEVICE_CHECKBOX = "//input[@id='trustDevice']"
    TRUST_DEVICE_CONTINUE = "//button[@id='continue']"
    # Cookie consent banner (OneTrust)
    COOKIE_ACCEPT = "//button[@id='onetrust-accept-btn-handler']"
    # Account details — transaction download
    DOWNLOAD_DROPDOWN = "//button[contains(@aria-label, 'ownload')]"
    DOWNLOAD_CSV = "//button[contains(text(), 'CSV') or contains(text(), 'csv')]"
    # Date range filter
    DATE_FILTER_BUTTON = "//button[contains(text(), 'Custom') or contains(@aria-label, 'ustom')]"
    DATE_FROM_INPUT = "//input[@aria-label='From date' or @name='fromDate' or @id='fromDate']"
    DATE_TO_INPUT = "//input[@aria-label='To date' or @name='toDate' or @id='toDate']"
    DATE_APPLY_BUTTON = "//button[contains(text(), 'Apply') or contains(text(), 'apply')]"
    # Transactions section
    TRANSACTIONS_SECTION = "//h2[contains(text(), 'TRANSACTIONS') or contains(text(), 'Transactions')]"
