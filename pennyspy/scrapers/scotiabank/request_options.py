from enum import StrEnum


class AccountType(StrEnum):
    CHEQUING = "chequing"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"


class DownloadFormat(StrEnum):
    CSV = "csv"
    # Others TBD based on what Scotiabank offers
