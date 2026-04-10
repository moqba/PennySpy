from datetime import datetime

from pennyspy.scrapers.scotiabank.request_options import DownloadFormat

_EXTENSION_MAP: dict[DownloadFormat, str] = {
    DownloadFormat.CSV: ".csv",
}


def get_default_filename(fmt: DownloadFormat) -> str:
    ext = _EXTENSION_MAP.get(fmt, ".csv")
    today = datetime.now().strftime("%Y-%m-%d")
    return f"scotiabank_transactions_{today}{ext}"
