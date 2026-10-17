"""``/jobs`` to follow a job, ``/exports`` to fetch what an export job produced."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from pennyspy.jobs.manager import Job, JobManager

_EVENT_POLL_SECONDS: Final[float] = 0.5
_HEARTBEAT_SECONDS: Final[float] = 15.0


def _get_job(manager: JobManager, job_id: str) -> Job:
    try:
        return manager.get(job_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=e.args[0])


def create_jobs_router(manager: JobManager) -> APIRouter:
    router = APIRouter()

    @router.get("/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return _get_job(manager, job_id).snapshot()

    @router.get("/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        """Server-sent events: the job's state each time it changes, ending once it finishes."""
        job = _get_job(manager, job_id)

        async def stream() -> AsyncIterator[str]:
            sent_version = -1
            idle = 0.0
            while True:
                if job.version != sent_version:
                    sent_version = job.version
                    idle = 0.0
                    yield f"data: {json.dumps(job.snapshot())}\n\n"
                    if job.finished:
                        return
                elif idle >= _HEARTBEAT_SECONDS:
                    idle = 0.0
                    # A comment line keeps proxies from closing a quiet stream.
                    yield ": keep-alive\n\n"
                if await request.is_disconnected():
                    return
                await asyncio.sleep(_EVENT_POLL_SECONDS)
                idle += _EVENT_POLL_SECONDS

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


def create_exports_router(manager: JobManager) -> APIRouter:
    router = APIRouter()

    @router.get("/{job_id}/file")
    def export_file(job_id: str, index: int = Query(default=0, ge=0)) -> FileResponse:
        """One file of a finished export, as an ordinary download. ``index`` picks among several."""
        job = _get_job(manager, job_id)
        if job.kind != "export":
            raise HTTPException(status_code=404, detail=f"Job {job_id} is not an export")
        if job.state == "failed":
            raise HTTPException(status_code=409, detail=f"Export {job_id} failed: {job.error}")
        if job.state != "done":
            raise HTTPException(status_code=409, detail=f"Export {job_id} is not ready yet ({job.stage})")
        if index >= len(job.files):
            raise HTTPException(status_code=404, detail=f"Export {job_id} has {len(job.files)} file(s)")
        path = job.files[index]
        if not path.exists():
            raise HTTPException(status_code=410, detail=f"{path.name} is no longer on the server")
        return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")

    return router
