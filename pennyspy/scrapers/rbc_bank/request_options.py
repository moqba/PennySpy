from enum import StrEnum


class Software(StrEnum):
    QUICKEN = "QUICKEN"
    MAKISOFT = "MAKISOFT"
    QUICKBOOKS = "QUICKBOOKS"
    MONEY = "MONEY"
    MAKISOFT_COMPTABILITY = "MAKISOFTB"
    SIMPLY_ACCOUNTING = "SIMPLYACCOUNTING"
    CSV = "EXCEL"

class SoftwareExtension(StrEnum):
    QUICKEN = ".ofx"
    MAKISOFT = ".afx"
    QUICKBOOKS = ".qbo"
    MONEY = ".ofx"
    MAKISOFT_COMPTABILITY = ".afx"
    SIMPLY_ACCOUNTING = ".aso"
    CSV = ".csv"

class AccountInfo(StrEnum):
    ALL_ACCOUNTS = "A"
    CHECKING_ACCOUNTS = "B"
    CREDIT_ACCOUNTS = "VALL"
    PRIMARY_CHECKING = "C001"
    SECONDARY_CHECKING = "C002"


class Include(StrEnum):
    NEW_OPERATIONS = "N"
    ALL_OPERATIONS = "A"
