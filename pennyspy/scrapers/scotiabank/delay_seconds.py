from enum import IntEnum


class DelaySeconds(IntEnum):
    PAGE_LOADING = 20
    COOKIE_INIT = 10
    LOGIN_SUCCESS_TIMEOUT = 120
    TWO_FACTOR_TIMEOUT = 5 * 60  # 300s — user has 5 min to approve on mobile app
    ACCOUNT_NAV_WAIT = 10
