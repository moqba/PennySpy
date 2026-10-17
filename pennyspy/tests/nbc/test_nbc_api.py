"""The NBC endpoints are registered next to Wealthsimple's."""

from __future__ import annotations

from pennyspy.pennyspy_api import app


def test_nbc_routes_are_registered() -> None:
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    for path in ("/nbc/login", "/nbc/login/jobs", "/nbc/verify", "/nbc/verify/jobs", "/nbc/export", "/nbc/scrape"):
        assert path in paths
