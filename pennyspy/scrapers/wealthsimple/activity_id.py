from enum import StrEnum

class ActivityXpath(StrEnum):
    ACTIVITY_CONTAINER = "//ws-card-loading-indicator//main"
    TRANSACTION_EXPENSION = "//button[contains(@id, '-header')]"
    DATE_HEADER = '//h2[@data-fs-privacy-rule="unmask"]'