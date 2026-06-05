import re
import time
from logging import getLogger
from typing import Final

import requests

logger = getLogger(__name__)

GITHUB_TAGS_URL: Final[str] = "https://api.github.com/repos/moqba/PennySpy/tags"
LATEST_VERSION_CACHE_TTL_SECONDS: Final[int] = 60 * 60

_latest_version_cache_expires_at = 0.0
_latest_version_cache_value: str | None = None


def version_sort_key(raw_version: str) -> tuple[int, ...]:
    normalized = raw_version.strip().removeprefix("refs/tags/").lstrip("vV")
    parts = []
    for part in normalized.split("."):
        match = re.match(r"\d+", part)
        if match is None:
            break
        parts.append(int(match.group(0)))
    return tuple(parts)


def is_newer_version(candidate: str | None, current: str) -> bool:
    if candidate is None or current == "unknown":
        return False

    candidate_key = version_sort_key(candidate)
    current_key = version_sort_key(current)
    if not candidate_key or not current_key:
        return False

    size = max(len(candidate_key), len(current_key))
    return candidate_key + (0,) * (size - len(candidate_key)) > current_key + (0,) * (size - len(current_key))


def get_latest_tag_version() -> str | None:
    global _latest_version_cache_expires_at, _latest_version_cache_value

    now = time.monotonic()
    if now < _latest_version_cache_expires_at:
        return _latest_version_cache_value

    try:
        response = requests.get(GITHUB_TAGS_URL, timeout=5)
        response.raise_for_status()
        tags = response.json()
    except (requests.RequestException, ValueError):
        logger.exception("Failed to fetch PennySpy release tags from GitHub")
        _latest_version_cache_expires_at = now + 300
        _latest_version_cache_value = None
        return None

    candidates = [
        tag["name"].strip().lstrip("vV")
        for tag in tags
        if isinstance(tag, dict) and isinstance(tag.get("name"), str) and version_sort_key(tag["name"])
    ]
    latest_version = max(candidates, key=version_sort_key, default=None)
    _latest_version_cache_expires_at = now + LATEST_VERSION_CACHE_TTL_SECONDS
    _latest_version_cache_value = latest_version
    return latest_version
