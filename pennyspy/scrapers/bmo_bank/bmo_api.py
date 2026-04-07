import shutil
import tempfile
import time
import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException, APIRouter, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pennyspy.scrapers.bmo_bank.bmo_bank import BMOBank
from pennyspy.scrapers.bmo_bank.request_options import AppType, StatementDate

from logging import getLogger

logger = getLogger(__name__)

router = APIRouter()

_SESSION_TTL_SECONDS = 600  # 10 minutes

_sessions: dict[str, tuple[BMOBank, float]] = {}


class BmoLoginParams(BaseModel):
    account_uuid: str


class BmoScrapeParams(BaseModel):
    session_id:     str
    otp_code:       str
    app_type:       AppType
    statement_date: Optional[StatementDate] = None
    until_date:     Optional[date] = None


def _cleanup_stale_sessions() -> None:
    now = time.monotonic()
    stale = [sid for sid, (_, ts) in _sessions.items() if now - ts > _SESSION_TTL_SECONDS]
    for sid in stale:
        bank, _ = _sessions.pop(sid)
        logger.warning("Cleaning up stale BMO session %s", sid)
        try:
            bank.quit()
        except Exception:
            pass


@router.post("/login")
def login(params: BmoLoginParams):
    _cleanup_stale_sessions()
    session_id = str(uuid.uuid4())
    bank = BMOBank()
    try:
        bank.initiate_login(account_uuid=params.account_uuid)
    except Exception as e:
        bank.quit()
        raise HTTPException(status_code=400, detail=str(e))
    _sessions[session_id] = (bank, time.monotonic())
    logger.info("BMO login initiated, session_id=%s", session_id)
    return {"session_id": session_id}


@router.post("/scrape")
def scrape_transactions(params: BmoScrapeParams, background_tasks: BackgroundTasks):
    entry = _sessions.get(params.session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Session not found. Call /bmo/login first.")
    bank, _ = entry

    is_csv_web = params.app_type == AppType.CSV_WEB
    if is_csv_web and params.until_date is None:
        raise HTTPException(status_code=400, detail="until_date is required for CSV from Web export")
    if not is_csv_web and params.statement_date is None:
        raise HTTPException(status_code=400, detail="statement_date is required for this export type")

    tmp_dirname = tempfile.mkdtemp()
    try:
        if is_csv_web:
            bank.complete_2fa(params.otp_code, skip_cookie_capture=True)
            transaction_file = bank.parse_transactions_from_web(
                until_date=datetime.combine(params.until_date, datetime.min.time()),
                export_directory=tmp_dirname,
            )
        else:
            bank.complete_2fa(params.otp_code)
            transaction_file = bank.download_transactions(
                app_type=params.app_type,
                statement_date=params.statement_date,
                export_directory=tmp_dirname,
            )
    except ValueError as e:
        _cleanup_session(params.session_id, bank)
        shutil.rmtree(tmp_dirname, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _cleanup_session(params.session_id, bank)
        shutil.rmtree(tmp_dirname, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

    _cleanup_session(params.session_id, bank)

    if not transaction_file.exists():
        shutil.rmtree(tmp_dirname, ignore_errors=True)
        raise HTTPException(status_code=404, detail="Transaction file was not created")

    background_tasks.add_task(shutil.rmtree, tmp_dirname)
    return FileResponse(
        path=transaction_file,
        filename=transaction_file.name,
        media_type="application/octet-stream",
    )


def _cleanup_session(session_id: str, bank: BMOBank) -> None:
    _sessions.pop(session_id, None)
    try:
        bank.quit()
    except Exception:
        pass
