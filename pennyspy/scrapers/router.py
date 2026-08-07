from __future__ import annotations

import shutil
import tempfile
from base64 import b64encode
from dataclasses import asdict
from logging import getLogger
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from pennyspy.scrapers.base import BankScraperInterface
from pennyspy.scrapers.export_store import prune_exports, retain_exports
from pennyspy.scrapers.session import ScraperSessionManager

logger = getLogger(__name__)


class VerifyParams(BaseModel):
    session_id: str
    otp_code: str | None = None


def create_scraper_router(
    *,
    scraper_type: type[BankScraperInterface],
    login_params_model: type[BaseModel] | None = None,
    scrape_params_model: type[BaseModel],
    session_manager: ScraperSessionManager,
) -> APIRouter:
    """Build a standard 3-endpoint router (login -> verify -> scrape) for any BankScraperInterface.

    ``scraper_type`` is the concrete scraper class.  It is used both as the
    factory (called with no arguments to create instances) and to scope
    sessions so that a session created for one bank cannot be used with
    another bank's endpoints.
    """

    router = APIRouter()

    def _get_scraper(session_id: str) -> BankScraperInterface:
        try:
            return session_manager.get(session_id, expected_type=scraper_type)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    if login_params_model is not None:

        @router.post("/login")
        def login_with_params(
            params: Annotated[BaseModel, Body()],
        ) -> dict[str, Any]:
            session_manager.close_all()
            scraper = scraper_type()
            login_kwargs = params.model_dump()
            try:
                step = scraper.start_auth(**login_kwargs)
            except Exception as e:
                logger.exception("%s login failed", scraper_type.__name__)
                scraper.quit()
                raise HTTPException(status_code=400, detail=str(e))
            session_id = session_manager.create(scraper)
            logger.info("Login initiated, session_id=%s", session_id)
            return {"session_id": session_id, **asdict(step)}

        # Override the endpoint's JSON schema to use the concrete model
        login_with_params.__annotations__["params"] = Annotated[login_params_model, Body()]

    else:

        @router.post("/login")
        def login_no_params() -> dict[str, Any]:
            session_manager.close_all()
            scraper = scraper_type()
            try:
                step = scraper.start_auth()
            except Exception as e:
                logger.exception("%s login failed", scraper_type.__name__)
                scraper.quit()
                raise HTTPException(status_code=400, detail=str(e))
            session_id = session_manager.create(scraper)
            logger.info("Login initiated, session_id=%s", session_id)
            return {"session_id": session_id, **asdict(step)}

    @router.post("/verify")
    def verify(params: Annotated[VerifyParams, Body()]) -> dict[str, Any]:
        scraper = _get_scraper(params.session_id)
        try:
            step = scraper.continue_auth(otp_code=params.otp_code)
        except Exception as e:
            session_manager.remove(params.session_id)
            raise HTTPException(status_code=400, detail=str(e))
        return {"session_id": params.session_id, **asdict(step)}

    def scrape(params: BaseModel, background_tasks: BackgroundTasks) -> Response:
        """Serve the files the scrape produced.

        One file is served as itself. Several — a bank that exports one file per account —
        are served as a JSON envelope of base64 contents, so every file reaches the caller
        intact in one response and the web UI can save each one separately. Packing them
        into an archive would only make the user unpack it again.

        Whatever is served is also retained on disk (see
        :mod:`pennyspy.scrapers.export_store`), so a scrape that reaches this point is not
        lost if the response never makes it to the browser.
        """
        scrape_kwargs: dict[str, Any] = params.model_dump()
        session_id: str = scrape_kwargs.pop("session_id")

        scraper = _get_scraper(session_id)

        prune_exports()
        tmp_dir = tempfile.mkdtemp()
        try:
            transaction_files = scraper.download_transaction_files(export_directory=Path(tmp_dir), **scrape_kwargs)
        except ValueError as e:
            logger.exception("%s scrape validation error for session %s", scraper_type.__name__, session_id)
            session_manager.remove(session_id)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("%s scrape failed for session %s", scraper_type.__name__, session_id)
            session_manager.remove(session_id)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=str(e))

        session_manager.remove(session_id)

        missing = [path for path in transaction_files if not path.exists()]
        if not transaction_files or missing:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            detail = "Transaction file was not created"
            if missing:
                detail = f"Transaction file was not created: {', '.join(path.name for path in missing)}"
            raise HTTPException(status_code=404, detail=detail)

        retain_exports(transaction_files, session_id)

        if len(transaction_files) > 1:
            # The contents go into the response body, so the temp directory can be dropped
            # right away instead of waiting on a background task.
            payload = {
                "files": [
                    {
                        "filename": path.name,
                        "content_base64": b64encode(path.read_bytes()).decode("ascii"),
                    }
                    for path in transaction_files
                ]
            }
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info(
                "Serving %d transaction file(s) for session %s: %s",
                len(transaction_files),
                session_id,
                ", ".join(path.name for path in transaction_files),
            )
            return JSONResponse(content=payload)

        background_tasks.add_task(shutil.rmtree, tmp_dir, True)
        return FileResponse(
            path=transaction_files[0],
            filename=transaction_files[0].name,
            media_type="application/octet-stream",
        )

    def scrape_endpoint(
        params: Annotated[BaseModel, Body()],
        background_tasks: BackgroundTasks,
    ) -> Response:
        return scrape(params, background_tasks)

    # FastAPI reads the request model off the endpoint's annotations, so the bank's concrete
    # model is substituted before the endpoint is registered.
    scrape_endpoint.__annotations__["params"] = Annotated[scrape_params_model, Body()]
    scrape_endpoint.__doc__ = scrape.__doc__
    router.post("/scrape")(scrape_endpoint)

    return router
