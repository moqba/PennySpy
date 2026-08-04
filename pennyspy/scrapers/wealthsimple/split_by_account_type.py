"""Splits a Wealthsimple export into one CSV per account type.

The export dialog usually writes one file per selected account, but not always: WS
sometimes answers a multi-account request with a single combined CSV, and every export
carries an ``account_type`` column naming the account each row belongs to. A combined file
is awkward to import — the destination expects one account per file — so a file that
carries the column is split along it, each part named after its account type
(``Chequing`` -> ``chequing-<original name>``).

The split is deliberately conservative: rows are copied field-for-field in their original
order, a file without the column is returned untouched, and a file that already holds a
single account type is renamed rather than rewritten so its bytes stay exactly as
Wealthsimple wrote them.
"""

from __future__ import annotations

import csv
import re
from collections import OrderedDict
from collections.abc import Iterable
from itertools import count
from logging import getLogger
from pathlib import Path
from typing import Final

logger = getLogger(__name__)

ACCOUNT_TYPE_COLUMN: Final[str] = "account_type"

# Rows whose account type is blank still have to land somewhere: dropping them would lose
# transactions, and an empty prefix would produce a file named "-<original name>".
UNKNOWN_ACCOUNT_TYPE: Final[str] = "unknown"

# The account type is file content, so it reaches the filesystem as a name: everything
# that is not a word character or a dash — spaces, but also "/" and ".." — becomes a dash.
_NON_NAME_CHARS: Final[re.Pattern[str]] = re.compile(r"[^\w-]+", re.UNICODE)


def account_type_slug(value: str) -> str:
    """The filename prefix for an ``account_type`` value: lowercase, spaces as dashes."""
    slug = _NON_NAME_CHARS.sub("-", value.strip().lower())
    return re.sub(r"-{2,}", "-", slug).strip("-") or UNKNOWN_ACCOUNT_TYPE


def _account_type_index(header: list[str]) -> int | None:
    for index, name in enumerate(header):
        if name.strip().lower() == ACCOUNT_TYPE_COLUMN:
            return index
    return None


def _unused_path(path: Path) -> Path:
    """``path``, or the first ``-2``/``-3``/… variant of it that is free.

    Two source files can name the same account type; the second must not overwrite the
    first, since that would silently drop an account's transactions."""
    if not path.exists():
        return path
    for suffix in count(2):
        candidate = path.with_name(f"{path.stem}-{suffix}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise AssertionError("unreachable")  # pragma: no cover


def _group_rows(rows: Iterable[list[str]], index: int) -> OrderedDict[str, list[list[str]]]:
    """Rows grouped by account-type slug, groups in order of first appearance."""
    groups: OrderedDict[str, list[list[str]]] = OrderedDict()
    for row in rows:
        if not row:
            continue
        value = row[index] if index < len(row) else ""
        groups.setdefault(account_type_slug(value), []).append(row)
    return groups


def split_by_account_type(path: Path) -> list[Path]:
    """Split ``path`` into one CSV per account type, next to the original.

    Returns the files that now hold the export's rows — ``[path]`` unchanged when the
    file has no ``account_type`` column, and the split parts otherwise. The original is
    removed once its rows have been written elsewhere, so no row is served twice.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            return [path]
        index = _account_type_index(header)
        if index is None:
            logger.info("%s has no %r column — serving it whole", path.name, ACCOUNT_TYPE_COLUMN)
            return [path]
        groups = _group_rows(reader, index)

    if not groups:
        return [path]

    if len(groups) == 1:
        slug = next(iter(groups))
        renamed = _unused_path(path.with_name(f"{slug}-{path.name}"))
        path.replace(renamed)
        logger.info("%s holds only %r activity — renamed to %s", path.name, slug, renamed.name)
        return [renamed]

    parts: list[Path] = []
    for slug, rows in groups.items():
        part = _unused_path(path.with_name(f"{slug}-{path.name}"))
        with part.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        parts.append(part)

    path.unlink()
    logger.info(
        "Split %s into %d file(s) by %s: %s",
        path.name,
        len(parts),
        ACCOUNT_TYPE_COLUMN,
        ", ".join(f"{part.name} ({len(groups[slug])} row(s))" for slug, part in zip(groups, parts)),
    )
    return parts


def split_exports_by_account_type(paths: list[Path]) -> list[Path]:
    """Apply :func:`split_by_account_type` to every export, keeping the order.

    A scrape costs a login, an OTP and minutes of waiting, so a file that cannot be split
    — unreadable, malformed, or in the way of its own output — is served as it is rather
    than failing the scrape.
    """
    result: list[Path] = []
    for path in paths:
        try:
            result.extend(split_by_account_type(path))
        except (OSError, csv.Error, UnicodeDecodeError):
            logger.exception("Failed to split %s by %s — serving it whole", path.name, ACCOUNT_TYPE_COLUMN)
            result.append(path)
    return result
