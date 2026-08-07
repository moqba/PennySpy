import json
import logging
import os
import pathlib
import tomllib
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from logging import getLogger
from typing import Any, Final, Literal

from dotenv import load_dotenv

load_dotenv()  # noqa: E402

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from pennyspy.logging_setup import setup_logging
from pennyspy.request_log import ResponseDeliveryLogger
from pennyspy.scrapers.bmo_bank.bmo_bank import BMOBank
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import AccountInfo, Include, Software
from pennyspy.scrapers.router import create_scraper_router
from pennyspy.scrapers.scotiabank.scotiabank import ScotiaBank
from pennyspy.scrapers.session import ScraperSessionManager
from pennyspy.scrapers.wealthsimple.wealthsimple import Wealthsimple
from pennyspy.version_check import get_latest_tag_version, get_release_notes, is_newer_version

LOG_FILE = setup_logging()
LOG_DIR = LOG_FILE.parent
logger = getLogger(__name__)

# ── Per-bank parameter models ─────────────────────────────────────────


class BmoLoginParams(BaseModel):
    account_uuids: list[str]

    @field_validator("account_uuids", mode="before")
    @classmethod
    def _coerce_account_uuids(cls, value: object) -> list[str]:
        # Accept a single string for backward compatibility with older clients.
        raw = [value] if isinstance(value, str) else value
        if not isinstance(raw, (list, tuple)):
            raise ValueError("account_uuids must be a string or a list of strings")
        cleaned = [str(item).strip() for item in raw if str(item).strip()]
        if not cleaned:
            raise ValueError("At least one account UUID is required")
        return cleaned


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
    """Wealthsimple's activity export, and optionally a daily earnings series beside it.

    ``account_ids`` are the ids in the account-details URL
    (``my.wealthsimple.com/app/account-details/tfsa-l0re4cur`` -> ``tfsa-l0re4cur``). Naming
    any adds one earnings CSV per account to the response, covering the same window
    ``since_date`` selects; leaving the list empty downloads the activity export alone.
    """

    session_id: str
    since_date: date
    account_ids: list[str] = []

    @field_validator("account_ids", mode="before")
    @classmethod
    def _coerce_account_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        raw = [value] if isinstance(value, str) else value
        if not isinstance(raw, (list, tuple)):
            raise ValueError("account_ids must be a string or a list of strings")
        return [str(item).strip() for item in raw if str(item).strip()]


# ── App setup ─────────────────────────────────────────────────────────

app = FastAPI()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


session_manager = ScraperSessionManager(ttl_seconds=600)


def _get_package_version() -> str:
    pyproject_path = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with pyproject_path.open("rb") as pyproject_file:
                pyproject = tomllib.load(pyproject_file)
            return str(pyproject["project"]["version"])
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            logger.exception("Failed to read package version from %s", pyproject_path)

    try:
        return version("pennyspy")
    except PackageNotFoundError:
        return "unknown"


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

# Added last so it wraps everything else and sees the bytes as they go out to the client.
app.add_middleware(ResponseDeliveryLogger)


class RevalidatedStaticFiles(StaticFiles):
    """Serve the web UI with revalidation forced on every request.

    Starlette sends an ETag but no ``Cache-Control``, so browsers fall back to heuristic
    freshness and decide for themselves how long a file stays good. That lets an upgrade
    apply to a page's HTML while its scripts are still served from cache — the page then
    runs two versions of itself, and the mismatch surfaces as an element one half expects
    and the other half has removed. Revalidating keeps them in step; an unchanged file
    still answers 304 and is not sent again.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


FRONTEND_DIR = pathlib.Path(os.getenv("FRONTEND_DIR", pathlib.Path(__file__).parent.parent / "frontend"))
if FRONTEND_DIR.exists():
    app.mount("/app", RevalidatedStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


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


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}


# ── Client-side diagnostics ───────────────────────────────────────────

client_logger = getLogger("pennyspy.client")

_CLIENT_LOG_MESSAGE_LIMIT: Final[int] = 500
_CLIENT_LOG_DETAIL_LIMIT: Final[int] = 4_000
_CLIENT_LOG_LEVELS: Final[dict[str, int]] = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
}


class ClientLogEntry(BaseModel):
    """A diagnostic the web UI could not show the user in time to be read."""

    level: Literal["error", "warning", "info"] = "error"
    message: str
    detail: dict[str, Any] | None = None


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… (truncated)"


@app.post("/client-log", tags=["Logs"])
def client_log(entry: ClientLogEntry) -> dict[str, str]:
    """Record a browser-side failure in the server log.

    A scrape error shown in the page is gone as soon as the user navigates away, which is
    how the one that prompted this went unread. Writing it to the same rotating log as the
    scrape itself puts both halves of the story in one place, at
    ``/logs`` and in the mounted data directory.
    """
    detail = _truncate(json.dumps(entry.detail, default=str), _CLIENT_LOG_DETAIL_LIMIT) if entry.detail else ""
    client_logger.log(
        _CLIENT_LOG_LEVELS[entry.level],
        "web UI: %s%s",
        _truncate(entry.message, _CLIENT_LOG_MESSAGE_LIMIT),
        f" | {detail}" if detail else "",
    )
    return {"status": "recorded"}


@app.get("/version", tags=["Version"])
def package_version() -> dict[str, str | bool | None]:
    current_version = _get_package_version()
    latest_version = get_latest_tag_version()
    update_available = is_newer_version(latest_version, current_version)
    release_notes = get_release_notes(latest_version) if update_available else None
    return {
        "name": "pennyspy",
        "version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_name": release_notes["release_name"] if release_notes else None,
        "release_notes": release_notes["release_notes"] if release_notes else None,
        "release_url": release_notes["release_url"] if release_notes else None,
        "release_published_at": release_notes["release_published_at"] if release_notes else None,
    }


@app.get("/", include_in_schema=False)
def read_root():
    return RedirectResponse(url="/app/index.html")


def run():
    uvicorn.run("pennyspy.pennyspy_api:app", host="0.0.0.0", port=API_PORT)
