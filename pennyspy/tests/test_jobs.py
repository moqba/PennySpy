"""Login, verify and export run as jobs the client follows, and exports download by job id."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pennyspy.credentials.store import CredentialStore
from pennyspy.jobs.manager import FINISHED_JOB_TTL_SECONDS, Job, JobManager
from pennyspy.jobs.router import create_exports_router, create_jobs_router
from pennyspy.scrapers import router as scraper_router
from pennyspy.scrapers.base import AuthStep, BankScraperInterface
from pennyspy.scrapers.router import create_scraper_router
from pennyspy.scrapers.session import ScraperSessionManager

_scraper_logger = logging.getLogger("pennyspy.scrapers.fake")


class _ScrapeParams(BaseModel):
    session_id: str
    file_count: int = 1
    fail: bool = False
    note: str | None = None


class _FakeScraper(BankScraperInterface):
    def __init__(self) -> None:
        self._notes: list[str] = []

    def start_auth(self, **kwargs: Any) -> AuthStep:
        _scraper_logger.info("Typing the redacted login into the fake bank")
        return AuthStep(status="needs_otp", message="Enter the code")

    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        if otp_code != "123456":
            raise ValueError("The bank rejected the code")
        return AuthStep(status="authenticated")

    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        raise NotImplementedError

    def download_transaction_files(
        self, *, export_directory: Path, file_count: int = 1, fail: bool = False, note: str | None = None, **kwargs: Any
    ) -> list[Path]:
        _scraper_logger.info("Downloading %d fake export(s)", file_count)
        self._notes = [note] if note else []
        if fail:
            raise RuntimeError("The export button never appeared")
        files = []
        for index in range(file_count):
            path = export_directory / f"activity_{index}.csv"
            path.write_bytes(f"date,amount\n2026-10-0{index + 1},{index}\n".encode())
            files.append(path)
        return files

    def export_notes(self) -> list[str]:
        return list(self._notes)

    def quit(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _info_logs(caplog: pytest.LogCaptureFixture) -> None:
    # The job detail is relayed from INFO records, which only reach handlers at INFO.
    caplog.set_level(logging.INFO, logger="pennyspy")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PENNYSPY_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("PENNYSPY_WSU", "env-user")
    monkeypatch.setenv("PENNYSPY_WSP", "env-pass")
    manager = JobManager()
    app = FastAPI()
    app.state.job_manager = manager
    app.include_router(
        create_scraper_router(
            bank="ws",
            scraper_type=_FakeScraper,
            scrape_params_model=_ScrapeParams,
            session_manager=ScraperSessionManager(),
            credential_store=CredentialStore(tmp_path / "logins"),
            job_manager=manager,
            display_name="Wealthsimple",
        ),
        prefix="/ws",
    )
    app.include_router(create_jobs_router(manager), prefix="/jobs")
    app.include_router(create_exports_router(manager), prefix="/exports")
    yield TestClient(app)
    manager.close()


def _follow(client: TestClient, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def _login_and_verify(client: TestClient) -> str:
    started = client.post("/ws/login/jobs")
    assert started.status_code == 202
    login = _follow(client, started.json()["job_id"])
    assert login["state"] == "done"
    assert login["auth"]["status"] == "needs_otp"

    verify = client.post("/ws/verify/jobs", json={"session_id": login["session_id"], "otp_code": "123456"})
    assert verify.status_code == 202
    assert _follow(client, verify.json()["job_id"])["auth"]["status"] == "authenticated"
    return login["session_id"]


def test_login_job_reports_what_the_scraper_is_doing(client: TestClient) -> None:
    started = client.post("/ws/login/jobs").json()
    assert started["kind"] == "login"
    assert started["state"] in ("queued", "running", "done")

    job = _follow(client, started["job_id"])

    assert job["detail"] == f"Login initiated, session_id={job['session_id']}"
    assert job["stage"] == "Done"
    assert job["session_id"]


def _hold_worker(manager: JobManager) -> threading.Event:
    """Keep the single worker busy until the returned event is set, so later jobs queue."""
    release = threading.Event()

    def hold(_ctx: Any) -> None:
        release.wait(5)

    manager.submit("login", "ws", hold)
    return release


def test_running_job_detail_comes_from_the_scraper_log(tmp_path: Path) -> None:
    manager = JobManager()
    seen: list[str] = []

    def work(ctx: Any) -> None:
        ctx.stage("Signing in")
        _scraper_logger.info("Clicking the sign-in button")
        seen.append(ctx.job.detail)

    job = manager.submit("login", "ws", work)
    assert job.wait(5)
    assert seen == ["Clicking the sign-in button"]
    assert job.state == "done"
    manager.close()


def test_close_stops_relaying_the_log() -> None:
    root = logging.getLogger("pennyspy")
    before = len(root.handlers)
    manager = JobManager()
    assert len(root.handlers) == before + 1
    manager.close()
    assert len(root.handlers) == before


def test_a_job_result_cannot_stand_in_for_the_job_fields() -> None:
    job = Job(id="j1", kind="login", bank="ws", result={"state": "done", "error": None, "session_id": "s1"})
    snapshot = job.snapshot()
    assert snapshot["state"] == "queued"
    assert snapshot["job_id"] == "j1"
    assert snapshot["session_id"] == "s1"


def test_export_jobs_outlive_other_finished_jobs() -> None:
    manager = JobManager()
    finished = datetime.now(UTC) - timedelta(seconds=FINISHED_JOB_TTL_SECONDS + 60)
    for job_id, kind in (("old-login", "login"), ("old-export", "export")):
        manager._jobs[job_id] = Job(id=job_id, kind=kind, bank="ws", state="done", finished_at=finished)  # type: ignore[arg-type]
    manager._prune()
    with pytest.raises(KeyError):
        manager.get("old-login")
    assert manager.get("old-export").kind == "export"
    manager.close()


def test_export_job_then_file_download(client: TestClient) -> None:
    session_id = _login_and_verify(client)

    started = client.post("/ws/export", json={"session_id": session_id})
    assert started.status_code == 202
    job = _follow(client, started.json()["job_id"])

    assert job["state"] == "done"
    assert [f["filename"] for f in job["files"]] == ["activity_0.csv"]
    download = client.get(job["files"][0]["url"])
    assert download.status_code == 200
    assert 'filename="activity_0.csv"' in download.headers["content-disposition"]
    assert download.text.startswith("date,amount")
    assert client.get(f"/exports/{job['job_id']}/file").content == download.content


def test_export_with_several_files_lists_each(client: TestClient) -> None:
    session_id = _login_and_verify(client)
    job = _follow(client, client.post("/ws/export", json={"session_id": session_id, "file_count": 3}).json()["job_id"])

    assert [f["index"] for f in job["files"]] == [0, 1, 2]
    assert client.get(f"/exports/{job['job_id']}/file?index=2").text.endswith("2026-10-03,2\n")
    assert client.get(f"/exports/{job['job_id']}/file?index=3").status_code == 404


def test_export_job_carries_the_scraper_notes(client: TestClient) -> None:
    session_id = _login_and_verify(client)
    job = _follow(
        client, client.post("/ws/export", json={"session_id": session_id, "note": "Starts later"}).json()["job_id"]
    )
    assert job["notes"] == ["Starts later"]

    session_id = _login_and_verify(client)
    assert _follow(client, client.post("/ws/export", json={"session_id": session_id}).json()["job_id"])["notes"] == []


def test_export_queued_behind_a_newer_login_fails_cleanly(client: TestClient) -> None:
    session_id = _login_and_verify(client)
    release = _hold_worker(client.app.state.job_manager)  # type: ignore[attr-defined]
    login_id = client.post("/ws/login/jobs").json()["job_id"]
    export = client.post("/ws/export", json={"session_id": session_id})
    assert export.status_code == 202  # the session still exists when the export is asked for
    release.set()

    assert _follow(client, login_id)["state"] == "done"
    job = _follow(client, export.json()["job_id"])
    assert job["state"] == "failed"
    assert "Start the login again" in job["error"]


def test_failed_export_reports_the_error(client: TestClient) -> None:
    session_id = _login_and_verify(client)
    job = _follow(client, client.post("/ws/export", json={"session_id": session_id, "fail": True}).json()["job_id"])

    assert job["state"] == "failed"
    assert "export button never appeared" in job["error"]
    assert client.get(f"/exports/{job['job_id']}/file").status_code == 409


def test_wrong_code_fails_the_verify_job(client: TestClient) -> None:
    login = _follow(client, client.post("/ws/login/jobs").json()["job_id"])
    job = _follow(
        client,
        client.post("/ws/verify/jobs", json={"session_id": login["session_id"], "otp_code": "000000"}).json()["job_id"],
    )
    assert job["state"] == "failed"
    assert job["error"] == "The bank rejected the code"


def test_unknown_session_and_job_are_404(client: TestClient) -> None:
    assert client.post("/ws/export", json={"session_id": "nope"}).status_code == 404
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/exports/nope/file").status_code == 404


def test_events_stream_ends_with_the_finished_job(client: TestClient) -> None:
    job_id = client.post("/ws/login/jobs").json()["job_id"]
    with client.stream("GET", f"/jobs/{job_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[len("data: ") :]) for line in response.iter_lines() if line.startswith("data: ")]
    assert events[-1]["state"] == "done"
    assert [e["version"] for e in events] == sorted(e["version"] for e in events)


def test_blocking_endpoint_gives_up_waiting_but_the_job_goes_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scraper_router, "BLOCKING_WAIT_SECONDS", 0)
    release = _hold_worker(client.app.state.job_manager)  # type: ignore[attr-defined]
    try:
        response = client.post("/ws/login")
    finally:
        release.set()
    assert response.status_code == 504
    job_id = response.json()["detail"].split()[1]
    assert _follow(client, job_id)["state"] == "done"


def test_blocking_endpoints_still_work(client: TestClient) -> None:
    login = client.post("/ws/login")
    assert login.status_code == 200
    assert login.json()["status"] == "needs_otp"
    session_id = login.json()["session_id"]

    assert client.post("/ws/verify", json={"session_id": session_id, "otp_code": "000000"}).status_code == 400

    login = client.post("/ws/login").json()
    assert (
        client.post("/ws/verify", json={"session_id": login["session_id"], "otp_code": "123456"}).json()["status"]
        == "authenticated"
    )
    scrape = client.post("/ws/scrape", json={"session_id": login["session_id"], "file_count": 2})
    assert scrape.status_code == 200
    assert [f["filename"] for f in scrape.json()["files"]] == ["activity_0.csv", "activity_1.csv"]
