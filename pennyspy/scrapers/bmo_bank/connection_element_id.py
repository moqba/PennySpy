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
