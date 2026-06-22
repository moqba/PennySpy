import json
import re
import urllib.request
from pathlib import Path

CHROME_VERSIONS_URL = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json"
DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"
MAX_CHROME_MAJOR_VERSION_LAG = 6


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as e:
        raise AssertionError(f"Chrome version must contain only numeric components: {version!r}") from e


def _dockerfile_chrome_version() -> str:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^ARG\s+CHROME_VERSION\s*=\s*([^\s#]+)", dockerfile, re.MULTILINE)
    assert match, "Dockerfile must define ARG CHROME_VERSION=<version>"
    return match.group(1)


def _latest_stable_chrome_version() -> str:
    request = urllib.request.Request(CHROME_VERSIONS_URL, headers={"User-Agent": "PennySpy-CI"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
        return payload["channels"]["Stable"]["version"]
    except Exception as e:
        raise AssertionError(f"Could not read the latest stable Chrome version from {CHROME_VERSIONS_URL}") from e


def test_dockerfile_chrome_version_is_not_more_than_six_releases_behind_stable():
    pinned_version = _dockerfile_chrome_version()
    latest_version = _latest_stable_chrome_version()
    pinned_major = _version_tuple(pinned_version)[0]
    latest_major = _version_tuple(latest_version)[0]

    assert latest_major - pinned_major <= MAX_CHROME_MAJOR_VERSION_LAG, (
        f"Dockerfile CHROME_VERSION is too old: pinned {pinned_version}, latest stable is {latest_version}. "
        f"The pinned version may be at most {MAX_CHROME_MAJOR_VERSION_LAG} major releases behind stable."
    )
