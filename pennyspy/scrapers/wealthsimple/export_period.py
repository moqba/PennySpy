"""The fixed set of look-back windows Wealthsimple's activity export offers."""

from __future__ import annotations

import calendar
from datetime import date
from enum import StrEnum
from typing import Final


class ExportPeriod(StrEnum):
    """Labels of the "Select period" dropdown, shortest window first."""

    LAST_3_MONTHS = "Last 3 months"
    LAST_6_MONTHS = "Last 6 months"
    LAST_12_MONTHS = "Last 12 months"


PERIOD_MONTHS: Final[dict[ExportPeriod, int]] = {
    ExportPeriod.LAST_3_MONTHS: 3,
    ExportPeriod.LAST_6_MONTHS: 6,
    ExportPeriod.LAST_12_MONTHS: 12,
}

LONGEST_PERIOD: Final[ExportPeriod] = ExportPeriod.LAST_12_MONTHS


def subtract_months(day: date, months: int) -> date:
    """``day`` shifted back by whole calendar months, clamped to the target month's length."""
    month_index = day.month - 1 - months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def select_export_period(since_date: date | None, *, today: date | None = None) -> ExportPeriod:
    """The shortest offered window that still reaches back to ``since_date``.

    Falls back to the longest window when ``since_date`` is missing or predates every
    option — the caller trims the merged rows to ``since_date`` afterwards either way,
    so a window that is too wide only costs download size, never correctness.
    """
    if since_date is None:
        return LONGEST_PERIOD
    reference = today or date.today()
    for period in ExportPeriod:
        if subtract_months(reference, PERIOD_MONTHS[period]) <= since_date:
            return period
    return LONGEST_PERIOD
