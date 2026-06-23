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
    # Chrome comes from the selenium base image; its tag (e.g. "148.0-20260505") leads with the
    # Chrome major.minor version, which is what we validate against the latest stable release.
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^ARG\s+SELENIUM_VERSION\s*=\s*(\d+(?:\.\d+)*)", dockerfile, re.MULTILINE)
    assert match, "Dockerfile must define ARG SELENIUM_VERSION=<chrome-version>[-<date>]"
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
        f"Selenium base image Chrome is too old: pinned {pinned_version}, latest stable is {latest_version}. "
        f"The pinned version may be at most {MAX_CHROME_MAJOR_VERSION_LAG} major releases behind stable."
    )
