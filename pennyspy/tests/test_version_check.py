from pennyspy import pennyspy_api, version_check


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

    assert pennyspy_api.package_version() == {
        "name": "pennyspy",
        "version": "0.5.10",
        "latest_version": "0.5.11",
        "update_available": True,
    }
