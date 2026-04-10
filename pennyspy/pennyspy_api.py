import os
import pathlib
from datetime import date, datetime
from logging import getLogger
from typing import Final

from dotenv import load_dotenv

load_dotenv()  # noqa: E402

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pennyspy.logging_setup import setup_logging
from pennyspy.scrapers.bmo_bank.bmo_bank import BMOBank
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import AccountInfo, Include, Software
from pennyspy.scrapers.router import create_scraper_router
from pennyspy.scrapers.scotiabank.scotiabank import ScotiaBank
from pennyspy.scrapers.session import ScraperSessionManager
from pennyspy.scrapers.wealthsimple.wealthsimple import Wealthsimple

LOG_FILE = setup_logging()
LOG_DIR = LOG_FILE.parent
logger = getLogger(__name__)

# ── Per-bank parameter models ─────────────────────────────────────────


class BmoLoginParams(BaseModel):
    account_uuid: str


class BmoScrapeParams(BaseModel):
    session_id: str
    app_type: AppType
    statement_date: StatementDate | None = None
    from_date: datetime | None = None


class RbcScrapeParams(BaseModel):
    session_id: str
    software: Software
    account_info: AccountInfo
    include: Include


class ScotiaScrapeParams(BaseModel):
    session_id: str
    from_date: date
    to_date: date


class WsScrapeParams(BaseModel):
    session_id: str
    since_date: date


# ── App setup ─────────────────────────────────────────────────────────

app = FastAPI()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


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
        scraper_type=ScotiaBank,
        scrape_params_model=ScotiaScrapeParams,
        session_manager=session_manager,
    ),
    prefix="/scotia",
    tags=["Scotiabank"],
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


def _list_log_files() -> list[pathlib.Path]:
    if not LOG_DIR.exists():
        return []
    files = [p for p in LOG_DIR.iterdir() if p.is_file() and p.name.startswith(LOG_FILE.name)]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


@app.get("/logs", tags=["Logs"])
def list_logs() -> dict:
    entries = []
    for path in _list_log_files():
        stat = path.stat()
        entries.append({"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
    return {"files": entries}


@app.get("/logs/content", tags=["Logs"])
def read_log(name: str = Query(...)) -> PlainTextResponse:
    allowed = {p.name for p in _list_log_files()}
    if name not in allowed:
        raise HTTPException(status_code=404, detail="Log file not found")
    target = LOG_DIR / name
    return PlainTextResponse(target.read_text(encoding="utf-8", errors="replace"))


@app.delete("/logs", tags=["Logs"])
def delete_logs() -> dict:
    deleted: list[str] = []
    for path in _list_log_files():
        if path.resolve() == LOG_FILE.resolve():
            with open(path, "w", encoding="utf-8"):
                pass
            deleted.append(path.name)
        else:
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError:
                logger.exception("Failed to delete log file %s", path)
    return {"deleted": deleted}


@app.get("/", include_in_schema=False)
def read_root():
    return RedirectResponse(url="/app/index.html")


def run():
    uvicorn.run("pennyspy.pennyspy_api:app", host="0.0.0.0", port=API_PORT)
