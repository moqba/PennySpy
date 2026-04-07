from enum import IntEnum


class DelaySeconds(IntEnum):
    PAGE_LOADING          = 20
    COOKIE_INIT           = 5
    PAGE_TIMEOUT          = 60
    COOKIE_BANNER_TIMEOUT = 10
    LOGIN_SUCCESS_TIMEOUT = 120
    ACCOUNT_NAV_WAIT      = 10
    TWO_FACTOR_TIMEOUT    = 5 * 60  # 300s — user has 5 min to receive + enter OTP
    MFA_STEP_TIMEOUT      = 15      # per-step wait for each 2FA UI element
    PAGINATION_WAIT       = 10
