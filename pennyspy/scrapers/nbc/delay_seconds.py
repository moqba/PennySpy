from enum import IntEnum


class DelaySeconds(IntEnum):
    PAGE_LOADING = 20
    PAGE_TIMEOUT = 60
    # The Didomi consent banner is drawn by a script that loads after the page, so it gets a
    # short look of its own on both domains. Most visits after the first never show it.
    COOKIE_PROMPT = 8
    # How long the entry page is given to show either the sign-in form or the "You can't
    # access this page from this link" page in front of it.
    ENTRY_PAGE = 45
    # How long the password is given to lead to the code page, the signed-in app or an error
    # on the sign-in form.
    LOGIN_OUTCOME = 90
    # How long a submitted code is given to lead to the signed-in app or the wrong-code error.
    OTP_OUTCOME = 2 * 60
    # The code page can still show the "You must enter a verification code" message from
    # before the code was typed; an error is only believed once this long after Confirm.
    OTP_ERROR_GRACE = 3
    TABLE_RELOAD = 60
    # The transaction table counts as loaded once its row count has stayed the same this long
    # with no spinner on screen.
    TABLE_SETTLE = 3
    # Cap on "show more" clicks or scroll rounds while reading the table, whichever applies.
    MAX_LOAD_MORE_ROUNDS = 200
    SIGN_OUT = 10
