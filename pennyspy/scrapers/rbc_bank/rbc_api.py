import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from pennyspy.scrapers.get_required_env_var import get_required_env_var
from pennyspy.scrapers.rbc_bank.rbc_bank import RBCBank
from pennyspy.scrapers.rbc_bank.request_options import Software, Include, AccountInfo

from logging import getLogger
logger = getLogger(__name__)

app = FastAPI()

API_PORT = int(get_required_env_var("PORT"))

class DownloadRequest(BaseModel):
    software: Software
    account_info: AccountInfo
    include: Include


@app.get("/")
def read_root():
    return {"message": "PennySpi API"}

@app.post("/scrape")
async def scrape_transactions(request: DownloadRequest):
    logger.info(request)
    logger.info("Got scrape request for RBC with software : %s, account info : %s, include : %s", request.software, request.account_info, request.include)
    bank = RBCBank()
    bank.get_session_cookies()
    try:
        transaction_file = bank.download_transactions(software=request.software, account_info=request.account_info, include=request.include)
    except Exception as e:
        raise HTTPException(status_code=404, detail=e)
    if not transaction_file.exists():
        raise HTTPException(status_code=404, detail="Transaction file is not found")
    return FileResponse(path=transaction_file, filename=request.filename, media_type="application/octet-stream")

def run():
    uvicorn.run("pennyspy.scrapers.rbc_bank.rbc_api:app", host="0.0.0.0", port=API_PORT)