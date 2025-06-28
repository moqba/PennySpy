import shutil
import tempfile

from fastapi import HTTPException, APIRouter, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import Software, Include, AccountInfo

from logging import getLogger
logger = getLogger(__name__)

router = APIRouter()

class RbcDownloadRequest(BaseModel):
    software: Software
    account_info: AccountInfo
    include: Include


@router.post("/scrape")
async def scrape_transactions(request: RbcDownloadRequest, background_tasks: BackgroundTasks):
    logger.info(request)
    logger.info("Got scrape request for RBC with software : %s, account info : %s, include : %s", request.software, request.account_info, request.include)
    bank = RBCBank()
    bank.get_session_cookies()
    tmp_dirname = tempfile.mkdtemp()
    try:
        transaction_file = bank.download_transactions(software=request.software, account_info=request.account_info, include=request.include, export_directory=tmp_dirname)
    except Exception as e:
        shutil.rmtree(tmp_dirname)
        raise HTTPException(status_code=404, detail=str(e))
    if not transaction_file.exists():
        shutil.rmtree(tmp_dirname)
        raise HTTPException(status_code=404, detail="Transaction file is not found")
    background_tasks.add_task(shutil.rmtree, tmp_dirname)
    return FileResponse(path=transaction_file, filename=transaction_file.name, media_type="application/octet-stream")