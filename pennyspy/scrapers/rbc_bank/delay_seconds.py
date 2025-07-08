from enum import IntEnum


class DelaySeconds(IntEnum):
    COOKIE_INIT = 10
    PAGE_LOADING = 20
    PAGE_TIMEOUT = 60
    COOKIE_PROMPT_TIMEOUT = 60
    TWO_FACTOR_TIMEOUT = 5*60