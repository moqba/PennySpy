from enum import StrEnum


class ConnectionElementId(StrEnum):
    USERNAME = "//fdc-input[@id='username']/div/div/input"
    PASSWORD = "//fdc-input[@id='password']/div/div/input"
    SIGN_IN = "//button[@aria-label='Sign in to Online Banking']"
    COOKIE_ACCEPT = "//button[@id='onetrust-accept-btn-handler']"
    LOGIN_ERROR_BANNER = "//div[contains(@class,'alert-danger')]"
    # 2FA flow
    MFA_NEXT_BUTTON    = "//otp-button//button[.//span[contains(@class,'text') and normalize-space(text())='NEXT']]"
    MFA_PHONE_RADIO    = "//label[span[text()='SMS text']]"
    MFA_AGREE_CHECKBOX = "//label[contains(@class,'checkbox-label')]//input[@type='checkbox']"
    MFA_SEND_CODE      = "//otp-button//button[.//span[contains(@class,'text') and normalize-space(text())='SEND CODE']]"
    OTP_INPUT          = "//input[@id='otp-input']"
    MFA_CONFIRM        = "//otp-button//button[.//span[contains(@class,'text') and normalize-space(text())='CONFIRM']]"
    MFA_CONTINUE       = "//button[contains(@class,'mercury') and .//span[contains(@class,'inner-span') and contains(normalize-space(text()),'CONTINUE')]]"
    # Web-parsed transaction table
    TRANSACTION_SECTION_HEADER = "tr.section-header-container"
    TRANSACTION_ROWS           = "//table//tbody/tr[contains(@class, 'table-row')]"
    TRANSACTION_ROW_INTERACTIVE = "tr.table-row.table-row-interactive"
    TRANSACTION_DATE           = ".//td[1]//span"
    TRANSACTION_DESC           = ".//td[2]//span"
    TRANSACTION_AMOUNT         = ".//td[3]//span[last()]"
    PAGINATION_NEXT_BUTTON     = "button[data-pagination-btn='true'].next-button"
