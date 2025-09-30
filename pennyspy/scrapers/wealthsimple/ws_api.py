import shutil
import tempfile
import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pennyspy.scrapers.wealthsimple.normalize_financial_data import normalize_financial_df
from pennyspy.scrapers.wealthsimple.wealthsimple import Wealthsimple

from logging import getLogger

logger = getLogger(__name__)

router = APIRouter()

_sessions: dict[str, Wealthsimple] = {}


class WsScrapeParams(BaseModel):
    session_id: str
    otp_code: str
    since_date: date
    format: Literal["csv"] = "csv"


@router.post("/login")
async def login():
    session_id = str(uuid.uuid4())
    ws = Wealthsimple()
    try:
        ws.login_request()
    except Exception as e:
        ws.quit()
        raise HTTPException(status_code=400, detail=str(e))
    _sessions[session_id] = ws
    logger.info("Login request sent, session_id=%s", session_id)
    return {"session_id": session_id}


@router.post("/scrape")
async def scrape_transactions(params: WsScrapeParams, background_tasks: BackgroundTasks):
    ws = _sessions.get(params.session_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Session not found. Call /ws/login first.")

    tmp_dirname = tempfile.mkdtemp()
    try:
        ws.send_2fa_text(params.otp_code)
        since_dt = datetime(params.since_date.year, params.since_date.month, params.since_date.day)
        df = ws.fetch_activity(since_date=since_dt)
        normalized = normalize_financial_df(df)
    except ValueError as e:
        _cleanup_session(params.session_id, ws)
        shutil.rmtree(tmp_dirname)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _cleanup_session(params.session_id, ws)
        shutil.rmtree(tmp_dirname)
        raise HTTPException(status_code=500, detail=str(e))

    _cleanup_session(params.session_id, ws)

    csv_path = f"{tmp_dirname}/wealthsimple_activity.csv"
    normalized.to_csv(csv_path, index=False)
    background_tasks.add_task(shutil.rmtree, tmp_dirname)
    return FileResponse(path=csv_path, filename="wealthsimple_activity.csv", media_type="text/csv")


def _cleanup_session(session_id: str, ws: Wealthsimple) -> None:
    _sessions.pop(session_id, None)
    try:
        ws.quit()
    except Exception:
        pass
