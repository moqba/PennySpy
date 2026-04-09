import shutil
import tempfile
from logging import getLogger

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import AccountInfo, Include, Software

logger = getLogger(__name__)

router = APIRouter()


class RbcDownloadParams(BaseModel):
    software: Software
    account_info: AccountInfo
    include: Include


@router.get("/scrape")
async def scrape_transactions(params: RbcDownloadParams = Depends(), background_tasks: BackgroundTasks):
    logger.info(params)
    logger.info(
        "Got scrape request for RBC with software : %s, account info : %s, include : %s",
        params.software,
        params.account_info,
        params.include,
    )
    try:
        bank = RBCBank()
        tmp_dirname = tempfile.mkdtemp()
        bank.get_session_cookies()
        transaction_file = bank.download_transactions(
            software=params.software,
            account_info=params.account_info,
            include=params.include,
            export_directory=tmp_dirname,
        )
    except Exception as e:
        bank.driver.quit()
        shutil.rmtree(tmp_dirname)
        raise HTTPException(status_code=404, detail=str(e))
    if transaction_file is None or not transaction_file.exists():
        shutil.rmtree(tmp_dirname)
        raise HTTPException(status_code=404, detail="Transaction file is not found")
    background_tasks.add_task(shutil.rmtree, tmp_dirname)
    bank.driver.quit()
    return FileResponse(path=transaction_file, filename=transaction_file.name, media_type="application/octet-stream")
