"""Keeps a copy of every scraped export on disk after the response has been built.

A scrape costs a login, an OTP and several minutes of waiting, and until now its only
output was the body of that one HTTP response: the temp directory was deleted before the
response reached the client, so anything that went wrong on the way to the browser
destroyed the export with no way to retry.

Files are retained under the data directory, which is already the volume-mounted path in
Docker, so a failed download can be picked up from the host instead of re-run. The
directory is resolved with the same ladder as the screenshot directory in
:mod:`pennyspy.scrapers.scraper`, and old exports are pruned so retention stays bounded.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from logging import getLogger
from pathlib import Path

from pennyspy.scrapers.scraper import _DOCKER_DATA_DIR, _ensure_artifact_dir

logger = getLogger(__name__)

_DOCKER_EXPORT_DIR = _DOCKER_DATA_DIR / "exports"
_EXPORT_DIR_ENV = "PENNYSPY_EXPORT_DIR"
DEFAULT_MAX_AGE_DAYS = 7


def _repo_checkout_export_dir() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "pennyspy-data" / "exports"
    return None


def _home_export_dir() -> Path:
    return Path.home() / ".pennyspy" / "exports"


def resolve_export_dir() -> Path:
    env_dir = os.environ.get(_EXPORT_DIR_ENV)
    if env_dir:
        return Path(env_dir)
    if _DOCKER_DATA_DIR.exists() and os.access(_DOCKER_DATA_DIR, os.W_OK):
        return _DOCKER_EXPORT_DIR
    checkout_dir = _repo_checkout_export_dir()
    if checkout_dir is not None:
        return checkout_dir
    return _home_export_dir()


def _ensure_export_dir(preferred: Path) -> Path:
    return _ensure_artifact_dir(preferred, _home_export_dir(), "exports")


def retain_exports(files: list[Path], session_id: str) -> Path | None:
    """Copy ``files`` into a timestamped directory and return it.

    Returns ``None`` if nothing could be retained; retention is a safety net, so a failure
    here must never take down a scrape that otherwise succeeded.
    """
    if not files:
        return None

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    destination = _ensure_export_dir(resolve_export_dir()) / f"{stamp}_{session_id[:8]}"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for path in files:
            shutil.copy2(path, destination / path.name)
    except OSError:
        logger.exception("Failed to retain %d export(s) in %s", len(files), destination)
        return None

    logger.info(
        "Retained %d export(s) in %s: %s",
        len(files),
        destination,
        ", ".join(path.name for path in files),
    )
    return destination


def prune_exports(max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> None:
    """Delete retained export directories older than ``max_age_days``."""
    export_dir = resolve_export_dir()
    if not export_dir.is_dir():
        return

    cutoff = time.time() - max_age_days * 86_400
    for entry in export_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            logger.debug("Failed to prune retained export %s", entry, exc_info=True)
            continue
        logger.info("Pruned retained export older than %d day(s): %s", max_age_days, entry)
