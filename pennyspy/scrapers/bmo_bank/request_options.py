from enum import StrEnum


class AppType(StrEnum):
    CSV        = "csv"
    MSMONEY    = "msmoney"
    QUICKEN    = "quicken"
    QUICKBOOKS = "quickbooks"
    SIMPLY_ACC = "simplyacc"


class AppTypeExtension(StrEnum):
    CSV        = ".csv"
    MSMONEY    = ".ofx"
    QUICKEN    = ".qfx"
    QUICKBOOKS = ".qbo"
    SIMPLY_ACC = ".aso"


class StatementDate(StrEnum):
    RECENT = "recent"
    ALL    = "all"
