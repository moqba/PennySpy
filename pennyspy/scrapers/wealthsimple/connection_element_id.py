from enum import StrEnum


class ConnectionElementXpath(StrEnum):
    USERNAME = '//input[@type="text" and @inputmode="email" and @aria-label="Log in email"]'
    PASSWORD = '//input[@type="password" and @inputmode="text"]'
    SUBMIT = '//button[@type="submit" and @role="button"]'
    USER_INCORRECT = '//p[contains(text(), "Your email or password was incorrect")]'
    PHONE_2FA = '//input[@type="text" and @autocomplete="one-time-code" and @placeholder="– – – – – –"]'
    FAILED_2FA = '//div[@role="alert" and contains(text(), "Try again or get a new code")]'


class ActivityElementXpath(StrEnum):
    LOAD_MORE = '//button[text()="Load more"]'
