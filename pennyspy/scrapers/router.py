from __future__ import annotations

import shutil
import tempfile
from dataclasses import asdict
from logging import getLogger
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pennyspy.scrapers.base import BankScraper
from pennyspy.scrapers.session import ScraperSessionManager

logger = getLogger(__name__)


class VerifyParams(BaseModel):
    session_id: str
    otp_code: str | None = None


def create_scraper_router(
    *,
    scraper_type: type[BankScraper],
    login_params_model: type[BaseModel] | None = None,
    scrape_params_model: type[BaseModel],
    session_manager: ScraperSessionManager,
) -> APIRouter:
    """Build a standard 3-endpoint router (login -> verify -> scrape) for any BankScraper.

    ``scraper_type`` is the concrete scraper class.  It is used both as the
    factory (called with no arguments to create instances) and to scope
    sessions so that a session created for one bank cannot be used with
    another bank's endpoints.
    """

    router = APIRouter()

    def _get_scraper(session_id: str) -> BankScraper:
        try:
            return session_manager.get(session_id, expected_type=scraper_type)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    if login_params_model is not None:

        @router.post("/login")
        def login_with_params(
            params: Annotated[BaseModel, Body()],
        ) -> dict[str, Any]:
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

    @router.post("/scrape")
    def scrape(
        params: Annotated[BaseModel, Body()],
        background_tasks: BackgroundTasks,
    ) -> FileResponse:
        scrape_kwargs: dict[str, Any] = params.model_dump()
        session_id: str = scrape_kwargs.pop("session_id")

        scraper = _get_scraper(session_id)

        tmp_dir = tempfile.mkdtemp()
        try:
            transaction_file = scraper.download_transactions(
                export_directory=Path(tmp_dir),
                **scrape_kwargs,
            )
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

        if not transaction_file.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=404, detail="Transaction file was not created")

        background_tasks.add_task(shutil.rmtree, tmp_dir, True)
        return FileResponse(
            path=transaction_file,
            filename=transaction_file.name,
            media_type="application/octet-stream",
        )

    # Override the scrape endpoint's annotation to use the concrete model
    scrape.__annotations__["params"] = Annotated[scrape_params_model, Body()]

    return router
