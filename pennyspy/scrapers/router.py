from __future__ import annotations

import shutil
import tempfile
from base64 import b64encode
from dataclasses import asdict
from logging import getLogger
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from pennyspy.credentials.resolve import LoginCredentials, LoginSelectionError, resolve_login
from pennyspy.credentials.store import CredentialNotFound, CredentialStore, CredentialStoreError
from pennyspy.jobs.manager import Job, JobContext, JobFailed, JobManager
from pennyspy.scrapers.base import BankScraperInterface
from pennyspy.scrapers.export_store import prune_exports, retain_exports
from pennyspy.scrapers.session import ScraperSessionManager

logger = getLogger(__name__)


class LoginParams(BaseModel):
    """Which saved login to sign in with. Leave it out to use the bank's only saved login, or
    the legacy env vars when none is saved (see :func:`pennyspy.credentials.resolve.resolve_login`)."""

    credential_id: str | None = None


class VerifyParams(BaseModel):
    session_id: str
    otp_code: str | None = None


def create_scraper_router(
    *,
    bank: str,
    scraper_type: type[BankScraperInterface],
    login_params_model: type[LoginParams] | None = None,
    scrape_params_model: type[BaseModel],
    session_manager: ScraperSessionManager,
    credential_store: CredentialStore,
    job_manager: JobManager,
    display_name: str | None = None,
) -> APIRouter:
    """Build the router for one bank: login -> verify -> export, each a background job.

    ``POST /login/jobs``, ``POST /verify/jobs`` and ``POST /export`` start a job and answer
    ``202`` with its id at once; the client follows it on ``/jobs/{id}`` and fetches an
    export's files from ``/exports/{id}/file`` (see :mod:`pennyspy.jobs`). The older
    ``/login``, ``/verify`` and ``/scrape`` run the same jobs and wait for them, for callers
    that want one blocking request.

    ``scraper_type`` is the concrete scraper class.  It is used both as the
    factory (called with no arguments to create instances) and to scope
    sessions so that a session created for one bank cannot be used with
    another bank's endpoints. ``bank`` is the key saved logins are filed under
    (``nbc``, ``ws``), and ``credential_store`` is where they are read
    from. ``login_params_model``, when given, must extend :class:`LoginParams`.
    """

    router = APIRouter()
    name = display_name or bank.upper()

    def _get_scraper(session_id: str) -> BankScraperInterface:
        try:
            return session_manager.get(session_id, expected_type=scraper_type)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    def _accepted(job: Job) -> JSONResponse:
        return JSONResponse(status_code=202, content=job.snapshot())

    def _wait(job: Job) -> Job:
        job.wait()
        if job.state == "failed":
            raise HTTPException(status_code=job.error_status or 500, detail=job.error)
        return job

    # ── Login ─────────────────────────────────────────────────────────

    def start_login(params: LoginParams | None) -> Job:
        login_kwargs = params.model_dump() if params is not None else {}
        credential_id: str | None = login_kwargs.pop("credential_id", None)
        # Picking the login is quick and its mistakes are the caller's, so they are reported
        # on this request rather than as a failed job.
        try:
            credentials = resolve_login(bank, credential_id, credential_store)
        except CredentialNotFound as e:
            raise HTTPException(status_code=404, detail=e.args[0])
        except (LoginSelectionError, CredentialStoreError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return job_manager.submit("login", bank, lambda ctx: run_login(ctx, credentials, login_kwargs))

    def run_login(ctx: JobContext, credentials: LoginCredentials, login_kwargs: dict[str, Any]) -> None:
        session_manager.close_all()
        ctx.stage(f"Signing in to {name}")
        scraper = scraper_type()
        try:
            step = scraper.start_auth(credentials=credentials, **login_kwargs)
        except Exception as e:
            logger.exception("%s login failed", scraper_type.__name__)
            scraper.quit()
            raise JobFailed(str(e)) from e
        session_id = session_manager.create(scraper)
        logger.info("Login initiated, session_id=%s", session_id)
        ctx.job.result = {"session_id": session_id, "auth": asdict(step)}

    def _login_response(job: Job) -> dict[str, Any]:
        return {"session_id": job.result["session_id"], **job.result["auth"]}

    if login_params_model is not None:

        def login_job_endpoint(params: Annotated[BaseModel, Body()]) -> JSONResponse:
            return _accepted(start_login(params))  # type: ignore[arg-type]

        def login_endpoint(params: Annotated[BaseModel, Body()]) -> dict[str, Any]:
            return _login_response(_wait(start_login(params)))  # type: ignore[arg-type]

        login_job_endpoint.__annotations__["params"] = Annotated[login_params_model, Body()]
        login_endpoint.__annotations__["params"] = Annotated[login_params_model, Body()]

    else:
        # Banks with nothing to choose but the login accept an empty body, as they always have.
        def login_job_endpoint(params: Annotated[LoginParams | None, Body()] = None) -> JSONResponse:  # type: ignore[misc]
            return _accepted(start_login(params))

        def login_endpoint(params: Annotated[LoginParams | None, Body()] = None) -> dict[str, Any]:  # type: ignore[misc]
            return _login_response(_wait(start_login(params)))

    router.post("/login/jobs", status_code=202)(login_job_endpoint)
    router.post("/login")(login_endpoint)

    # ── Verify ────────────────────────────────────────────────────────

    def start_verify(params: VerifyParams) -> Job:
        scraper = _get_scraper(params.session_id)

        def run(ctx: JobContext) -> None:
            ctx.stage("Checking the code" if params.otp_code else f"Checking the {name} session")
            try:
                step = scraper.continue_auth(otp_code=params.otp_code)
            except Exception as e:
                session_manager.remove(params.session_id)
                raise JobFailed(str(e)) from e
            ctx.job.result = {"session_id": params.session_id, "auth": asdict(step)}

        return job_manager.submit("verify", bank, run)

    @router.post("/verify/jobs", status_code=202)
    def verify_job(params: Annotated[VerifyParams, Body()]) -> JSONResponse:
        return _accepted(start_verify(params))

    @router.post("/verify")
    def verify(params: Annotated[VerifyParams, Body()]) -> dict[str, Any]:
        job = _wait(start_verify(params))
        return {"session_id": params.session_id, **job.result["auth"]}

    # ── Export ────────────────────────────────────────────────────────

    def start_export(params: BaseModel) -> Job:
        scrape_kwargs: dict[str, Any] = params.model_dump()
        session_id: str = scrape_kwargs.pop("session_id")
        scraper = _get_scraper(session_id)

        def run(ctx: JobContext) -> None:
            ctx.stage(f"Exporting from {name}")
            ctx.job.files = run_export(ctx.job, scraper, session_id, scrape_kwargs)

        return job_manager.submit("export", bank, run)

    def run_export(
        job: Job, scraper: BankScraperInterface, session_id: str, scrape_kwargs: dict[str, Any]
    ) -> list[Path]:
        """Download the files and keep them where ``/exports/{id}/file`` can serve them.

        They are served from the retained copy (see :mod:`pennyspy.scrapers.export_store`),
        which outlives the job and the container. Should retention fail, they stay in the
        job's own temp directory instead, removed when the job is forgotten.
        """
        prune_exports()
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            transaction_files = scraper.download_transaction_files(export_directory=tmp_dir, **scrape_kwargs)
        except ValueError as e:
            logger.exception("%s scrape validation error for session %s", scraper_type.__name__, session_id)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise JobFailed(str(e)) from e
        except Exception:
            logger.exception("%s scrape failed for session %s", scraper_type.__name__, session_id)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        finally:
            session_manager.remove(session_id)

        missing = [path for path in transaction_files if not path.exists()]
        if not transaction_files or missing:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            detail = "Transaction file was not created"
            if missing:
                detail = f"Transaction file was not created: {', '.join(path.name for path in missing)}"
            raise JobFailed(detail, status_code=404)

        logger.info("Export ready for job %s: %s", job.id, ", ".join(path.name for path in transaction_files))
        retained = retain_exports(transaction_files, session_id)
        if retained is None:
            job.scratch_dir = tmp_dir
            return transaction_files
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return [retained / path.name for path in transaction_files]

    def export_endpoint(params: Annotated[BaseModel, Body()]) -> JSONResponse:
        return _accepted(start_export(params))

    def scrape(params: BaseModel) -> Response:
        """Run the export and wait for it, serving the files in the response.

        One file is served as itself. Several — a bank that exports one file per account —
        are served as a JSON envelope of base64 contents, so every file reaches the caller
        intact in one response. ``POST /export`` is the non-blocking way to do the same.
        """
        job = _wait(start_export(params))
        if len(job.files) > 1:
            logger.info(
                "Serving %d transaction file(s) for job %s: %s",
                len(job.files),
                job.id,
                ", ".join(path.name for path in job.files),
            )
            return JSONResponse(
                content={
                    "files": [
                        {"filename": path.name, "content_base64": b64encode(path.read_bytes()).decode("ascii")}
                        for path in job.files
                    ]
                }
            )
        return FileResponse(path=job.files[0], filename=job.files[0].name, media_type="application/octet-stream")

    def scrape_endpoint(params: Annotated[BaseModel, Body()]) -> Response:
        return scrape(params)

    # FastAPI reads the request model off the endpoint's annotations, so the bank's concrete
    # model is substituted before the endpoints are registered.
    export_endpoint.__annotations__["params"] = Annotated[scrape_params_model, Body()]
    export_endpoint.__doc__ = "Start an export job; follow it on /jobs/{job_id}, then fetch /exports/{job_id}/file."
    router.post("/export", status_code=202)(export_endpoint)
    scrape_endpoint.__annotations__["params"] = Annotated[scrape_params_model, Body()]
    scrape_endpoint.__doc__ = scrape.__doc__
    router.post("/scrape")(scrape_endpoint)

    return router
