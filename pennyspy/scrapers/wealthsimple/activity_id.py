from enum import StrEnum


class ActivityXpath(StrEnum):
    ACTIVITY_CONTAINER = "//ws-card-loading-indicator//main"
    TRANSACTION_EXPENSION = "//button[contains(@id, '-header')]"
    DATE_HEADER = '//h2[@data-fs-privacy-rule="unmask"]'


class ActivityCss(StrEnum):
    # Requires aria-controls so only real accordion header buttons match — never
    # auxiliary controls inside an expanded panel (e.g. "Add a note").
    HEADER_BUTTON = 'button[id*="-header"][aria-controls]'
    HEADER_BUTTON_FOR_REGION = 'button[aria-controls="{region_id}"]'
