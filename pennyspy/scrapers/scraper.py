"""Shared browser configuration and failure-artifact helpers.

The browser engine itself lives in :mod:`pennyspy.scrapers.zen_scraper`; this module
holds the engine-independent pieces: :class:`BrowserConfig`, delay pacing, and the
screenshot / failure-HTML directory resolution used when a scrape step fails.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_HEADLESS: bool = os.environ.get("PENNYSPY_HEADLESS", "true").strip().lower() not in {"false", "0", "no"}
_DOCKER_DATA_DIR = Path("/app/data")
_DOCKER_SCREENSHOT_DIR = _DOCKER_DATA_DIR / "screenshots"
_FAILURE_HTML_DIR_ENV = "PENNYSPY_FAILURE_HTML_DIR"


@dataclass(frozen=True)
class DelayRange:
    """Validated range of delay values in seconds."""

    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if self.minimum < 0 or self.maximum < 0:
            raise ValueError("delays must be non-negative")
        if self.minimum > self.maximum:
            raise ValueError("delay minimum cannot exceed maximum")

    def sample(self) -> float:
        return random.uniform(self.minimum, self.maximum)


@dataclass(frozen=True)
class BrowserConfig:
    """Per-scraper browser configuration."""

    headless: bool = DEFAULT_HEADLESS
    extra_arguments: list[str] = field(default_factory=list)
    action_delay: DelayRange = field(default_factory=lambda: DelayRange(0.4, 1.0))
    typing_delay: DelayRange = field(default_factory=lambda: DelayRange(0.04, 0.12))


def _repo_checkout_screenshot_dir() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "pennyspy-data" / "screenshots"
    return None


def _home_screenshot_dir() -> Path:
    return Path.home() / ".pennyspy" / "screenshots"


def _home_failure_html_dir() -> Path:
    return Path.home() / ".pennyspy" / "failure-html"


def _resolve_screenshot_dir() -> Path:
    env_dir = os.environ.get("PENNYSPY_SCREENSHOT_DIR")
    if env_dir:
        return Path(env_dir)
    if _DOCKER_DATA_DIR.exists() and os.access(_DOCKER_DATA_DIR, os.W_OK):
        return _DOCKER_SCREENSHOT_DIR
    checkout_dir = _repo_checkout_screenshot_dir()
    if checkout_dir is not None:
        return checkout_dir
    return _home_screenshot_dir()


def _resolve_failure_html_dir() -> Path | None:
    env_dir = os.environ.get(_FAILURE_HTML_DIR_ENV)
    if not env_dir:
        return None
    return Path(env_dir)


def _ensure_artifact_dir(preferred: Path, fallback_root: Path, label: str) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except PermissionError as exc:
        fallback = fallback_root / preferred.name
        logger.warning(
            "cannot write %s to %s (%s); falling back to %s",
            label,
            preferred,
            exc,
            fallback,
        )
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _ensure_screenshot_dir(preferred: Path) -> Path:
    return _ensure_artifact_dir(preferred, _home_screenshot_dir(), "screenshots")


def _ensure_failure_html_dir(preferred: Path) -> Path:
    return _ensure_artifact_dir(preferred, _home_failure_html_dir(), "failure HTML")


def _verification_screenshot_name(description: str) -> str:
    slug = "".join(char if char.isalnum() else "_" for char in description.lower()).strip("_")
    return f"verify_failed_{slug}" if slug else "verify_failed"
