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
