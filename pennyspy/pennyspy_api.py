import os
import pathlib
from datetime import date, datetime
from logging import getLogger
from typing import Final

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pennyspy.scrapers.bmo_bank.bmo_bank import BMOBank
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import AccountInfo, Include, Software
from pennyspy.scrapers.router import create_scraper_router
from pennyspy.scrapers.session import ScraperSessionManager
from pennyspy.scrapers.wealthsimple.wealthsimple import Wealthsimple

logger = getLogger(__name__)

# ── Per-bank parameter models ─────────────────────────────────────────


class BmoLoginParams(BaseModel):
    account_uuid: str


class BmoScrapeParams(BaseModel):
    session_id: str
    app_type: AppType
    statement_date: StatementDate | None = None
    until_date: datetime | None = None


class RbcScrapeParams(BaseModel):
    session_id: str
    software: Software
    account_info: AccountInfo
    include: Include


class WsScrapeParams(BaseModel):
    session_id: str
    since_date: date


# ── App setup ─────────────────────────────────────────────────────────

app = FastAPI()

session_manager = ScraperSessionManager(ttl_seconds=600)

app.include_router(
    create_scraper_router(
        scraper_type=BMOBank,
        login_params_model=BmoLoginParams,
        scrape_params_model=BmoScrapeParams,
        session_manager=session_manager,
    ),
    prefix="/bmo",
    tags=["BMO"],
)

app.include_router(
    create_scraper_router(
        scraper_type=RBCBank,
        scrape_params_model=RbcScrapeParams,
        session_manager=session_manager,
    ),
    prefix="/rbc",
    tags=["RBC"],
)

app.include_router(
    create_scraper_router(
        scraper_type=Wealthsimple,
        scrape_params_model=WsScrapeParams,
        session_manager=session_manager,
    ),
    prefix="/ws",
    tags=["Wealthsimple"],
)

API_PORT: Final[int] = int(os.getenv("PENNYSPY_PORT", "5056"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = pathlib.Path(os.getenv("FRONTEND_DIR", pathlib.Path(__file__).parent.parent / "frontend"))
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.get("/", include_in_schema=False)
def read_root():
    return RedirectResponse(url="/app/index.html")


def run():
    uvicorn.run("pennyspy.pennyspy_api:app", host="0.0.0.0", port=API_PORT)
