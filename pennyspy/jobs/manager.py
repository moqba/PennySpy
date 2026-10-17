"""Long browser actions — signing in, checking a code, exporting — run as background jobs.

A login or an export can hold a browser for minutes. Doing it inside one HTTP request left
the page with nothing to show but a spinner, and a connection that dropped in that window
lost the result. Each action now starts a job and answers at once with its id; the client
follows the job (``GET /jobs/{id}`` or the ``/events`` stream) and, for an export, fetches
the files with ``GET /exports/{id}/file`` once the job is done.

Jobs run one at a time on a single worker thread: there is only ever one browser session,
and a login closes whatever session came before it. While a job runs, the INFO lines the
scraper logs from that thread become the job's ``detail``, so the page can say what the
browser is doing right now instead of only that it is busy. Those lines go to the Logs page
already, and never carry a credential (see AGENTS.md).
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging import getLogger
from pathlib import Path
from typing import Any, Final, Literal

from pennyspy.scrapers.export_store import DEFAULT_MAX_AGE_DAYS

logger = getLogger(__name__)

JobKind = Literal["login", "verify", "export"]
JobState = Literal["queued", "running", "done", "failed"]

FINISHED_JOB_TTL_SECONDS: Final[int] = 3600
# An export job is kept as long as its retained files are, so /exports/{id}/file keeps
# working for as long as the copy on disk exists. Jobs live in memory: a restart drops them.
FINISHED_EXPORT_TTL_SECONDS: Final[int] = DEFAULT_MAX_AGE_DAYS * 86_400
_DETAIL_LIMIT: Final[int] = 240


class JobFailed(Exception):
    """A job failed for a reason the caller should see, with the HTTP status that fits it."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Job:
    id: str
    kind: JobKind
    bank: str
    state: JobState = "queued"
    stage: str = "Waiting for the browser"
    detail: str = ""
    error: str | None = None
    error_status: int | None = None
    result: dict[str, Any] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    # A directory only this job's files live in, removed when the job is forgotten.
    scratch_dir: Path | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    version: int = 0
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed")

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def snapshot(self) -> dict[str, Any]:
        """What the client is shown. Paths on the server stay on the server."""
        # The job's own fields go last, so nothing a job returns can stand in for them.
        snapshot: dict[str, Any] = {
            **self.result,
            "job_id": self.id,
            "kind": self.kind,
            "bank": self.bank,
            "state": self.state,
            "stage": self.stage,
            "detail": self.detail,
            "error": self.error,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "finished_at": self.finished_at.isoformat(timespec="seconds") if self.finished_at else None,
            "version": self.version,
        }
        if self.kind == "export":
            snapshot["files"] = [
                {
                    "index": index,
                    "filename": path.name,
                    "size": path.stat().st_size if path.exists() else None,
                    "url": f"/exports/{self.id}/file?index={index}",
                }
                for index, path in enumerate(self.files)
            ]
        return snapshot


class JobContext:
    """Handed to a running job so it can say what it is doing."""

    def __init__(self, manager: JobManager, job: Job) -> None:
        self._manager = manager
        self.job = job

    def stage(self, stage: str) -> None:
        self._manager._update(self.job, stage=stage, detail="")


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pennyspy-job")
        self._running_by_thread: dict[int, Job] = {}
        self._relay = _JobDetailRelay(self)
        logging.getLogger("pennyspy").addHandler(self._relay)

    def close(self) -> None:
        """Stop relaying log lines and stop the worker once its current job is done."""
        logging.getLogger("pennyspy").removeHandler(self._relay)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def submit(self, kind: JobKind, bank: str, work: Callable[[JobContext], None]) -> Job:
        self._prune()
        job = Job(id=str(uuid.uuid4()), kind=kind, bank=bank)
        with self._lock:
            busy = any(not other.finished for other in self._jobs.values())
            self._jobs[job.id] = job
        if busy:
            job.stage = "Waiting for the previous job to finish"
        logger.info("Queued %s %s job %s", bank, kind, job.id)
        self._executor.submit(self._run, job, work)
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Job {job_id} not found")
        return job

    def _run(self, job: Job, work: Callable[[JobContext], None]) -> None:
        thread_id = threading.get_ident()
        with self._lock:
            self._running_by_thread[thread_id] = job
        self._update(job, state="running", started_at=datetime.now(UTC))
        try:
            work(JobContext(self, job))
        except JobFailed as e:
            logger.warning("%s %s job %s failed: %s", job.bank, job.kind, job.id, e)
            self._finish(job, error=str(e), error_status=e.status_code)
        except Exception as e:
            logger.exception("%s %s job %s failed", job.bank, job.kind, job.id)
            self._finish(job, error=str(e) or type(e).__name__, error_status=500)
        else:
            logger.info("%s %s job %s done", job.bank, job.kind, job.id)
            self._finish(job)
        finally:
            with self._lock:
                self._running_by_thread.pop(thread_id, None)

    def _finish(self, job: Job, *, error: str | None = None, error_status: int | None = None) -> None:
        self._update(
            job,
            state="failed" if error else "done",
            stage="Failed" if error else "Done",
            error=error,
            error_status=error_status,
            finished_at=datetime.now(UTC),
        )
        job._done.set()

    def _update(self, job: Job, **changes: Any) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(job, name, value)
            job.version += 1

    def _relay_detail(self, thread_id: int, message: str) -> None:
        with self._lock:
            job = self._running_by_thread.get(thread_id)
        if job is not None:
            self._update(job, detail=message[:_DETAIL_LIMIT])

    def _prune(self) -> None:
        now = time.time()
        with self._lock:
            stale = [
                job
                for job in self._jobs.values()
                if job.finished and job.finished_at is not None and job.finished_at.timestamp() < now - _ttl(job)
            ]
            for job in stale:
                del self._jobs[job.id]
        for job in stale:
            if job.scratch_dir is not None:
                shutil.rmtree(job.scratch_dir, ignore_errors=True)


def _ttl(job: Job) -> int:
    return FINISHED_EXPORT_TTL_SECONDS if job.kind == "export" else FINISHED_JOB_TTL_SECONDS


class _JobDetailRelay(logging.Handler):
    """Copies each INFO line a job's thread logs into that job's ``detail``."""

    def __init__(self, manager: JobManager) -> None:
        super().__init__(level=logging.INFO)
        self._manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:
            return
        try:
            self._manager._relay_detail(record.thread or 0, record.getMessage())
        except Exception:
            self.handleError(record)
