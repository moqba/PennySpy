from enum import IntEnum


class DelaySeconds(IntEnum):
    COOKIE_INIT = 10
    LOGIN_ATTEMPT = 5
    ACTION_REFRESH = 2
    ROW_RENDER = 10
    EXPORT_STEP = 15
    PAGE_LOADING = 20
    PAGE_TIMEOUT = 60
    # WS generates the export server-side before the browser download starts, once per
    # account, so an account-heavy export trickles in over several minutes.
    DOWNLOAD_TIMEOUT = 300
    # A single "Download CSV" click can yield several files; how long the finished
    # set must stay unchanged before the download is considered complete.
    DOWNLOAD_SETTLE = 8
    # While fewer files than accounts have arrived, wait this long instead: WS builds each
    # account's export server-side, so minutes-long gaps with no download in flight are
    # normal and the short settle would return only the accounts that finished first.
    DOWNLOAD_INCOMPLETE_SETTLE = 60
    COOKIE_PROMPT_TIMEOUT = 60
    TWO_FACTOR_TIMEOUT = 5 * 60
