from pennyspy import pennyspy_api, version_check


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_version_sort_key_handles_tag_prefixes():
    assert version_check.version_sort_key("v0.5.10") > version_check.version_sort_key("0.5.9")
    assert version_check.version_sort_key("refs/tags/v1.0.0") == (1, 0, 0)


def test_is_newer_version_pads_shorter_versions():
    assert version_check.is_newer_version("0.6", "0.5.10")
    assert not version_check.is_newer_version("0.5.10", "0.5.10")
    assert not version_check.is_newer_version("0.5.9", "0.5.10")


def test_package_version_reports_available_update(monkeypatch):
    monkeypatch.setattr(pennyspy_api, "_get_package_version", lambda: "0.5.10")
    monkeypatch.setattr(pennyspy_api, "get_latest_tag_version", lambda: "0.5.11")
    monkeypatch.setattr(
        pennyspy_api,
        "get_release_notes",
        lambda version: {
            "release_name": "Release v0.5.11",
            "release_notes": "### Added\n\n- Version notice",
            "release_url": "https://github.com/moqba/PennySpy/releases/tag/v0.5.11",
            "release_published_at": "2026-06-05T00:00:00Z",
        },
    )

    assert pennyspy_api.package_version() == {
        "name": "pennyspy",
        "version": "0.5.10",
        "latest_version": "0.5.11",
        "update_available": True,
        "release_name": "Release v0.5.11",
        "release_notes": "### Added\n\n- Version notice",
        "release_url": "https://github.com/moqba/PennySpy/releases/tag/v0.5.11",
        "release_published_at": "2026-06-05T00:00:00Z",
    }


def test_package_version_skips_release_notes_when_current(monkeypatch):
    monkeypatch.setattr(pennyspy_api, "_get_package_version", lambda: "0.5.11")
    monkeypatch.setattr(pennyspy_api, "get_latest_tag_version", lambda: "0.5.11")
    monkeypatch.setattr(
        pennyspy_api,
        "get_release_notes",
        lambda version: (_ for _ in ()).throw(AssertionError("release notes should not be fetched")),
    )

    assert pennyspy_api.package_version() == {
        "name": "pennyspy",
        "version": "0.5.11",
        "latest_version": "0.5.11",
        "update_available": False,
        "release_name": None,
        "release_notes": None,
        "release_url": None,
        "release_published_at": None,
    }


def test_get_release_notes_reads_github_release(monkeypatch):
    version_check._release_notes_cache.clear()

    def fake_get(url, timeout):
        assert url == "https://api.github.com/repos/moqba/PennySpy/releases/tags/v0.6.1"
        assert timeout == 5
        return FakeResponse(
            {
                "name": "v0.6.1",
                "body": "### Changed\n\n- Shared browser engine",
                "html_url": "https://github.com/moqba/PennySpy/releases/tag/v0.6.1",
                "published_at": "2026-07-02T00:00:00Z",
            }
        )

    monkeypatch.setattr(version_check.requests, "get", fake_get)

    assert version_check.get_release_notes("0.6.1") == {
        "release_name": "v0.6.1",
        "release_notes": "### Changed\n\n- Shared browser engine",
        "release_url": "https://github.com/moqba/PennySpy/releases/tag/v0.6.1",
        "release_published_at": "2026-07-02T00:00:00Z",
    }


def test_get_release_notes_returns_none_on_github_failure(monkeypatch):
    version_check._release_notes_cache.clear()

    def fake_get(url, timeout):
        raise version_check.requests.RequestException("network down")

    monkeypatch.setattr(version_check.requests, "get", fake_get)

    assert version_check.get_release_notes("0.6.1") is None
