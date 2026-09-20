from enum import IntEnum


class DelaySeconds(IntEnum):
    COOKIE_INIT = 10
    LOGIN_ATTEMPT = 5
    ACTION_REFRESH = 2
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
    # How long a chart-toolbar tab is waited for on the account-details page. Short on
    # purpose: the tabs only steer the graph the page draws for itself, so a missing one is
    # worth a brief look and no more.
    GRAPH_TAB = 15
    # How long the account-details page is given to issue its own account-graph query, which
    # is what the header hook needs to see before any earnings query can be sent.
    GRAPH_HEADERS = 45
    # Cap on one account-graph query, enforced in the page so a request that never answers
    # cannot hang the scrape.
    GRAPH_REQUEST = 60
