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
    software: str
    account_info: str
    include: str

    @field_validator("software")
    @classmethod
    def software_validator(cls, v):
        if v not in Software.__members__:
            raise ValueError(f"'{v}' is not a valid software. Must be one of {list(Software.__members__.keys())}")

    @field_validator("account_info")
    @classmethod
    def account_info_validator(cls, v):
        if v not in AccountInfo.__members__:
            raise ValueError(f"'{v}' is not a valid account info. Must be one of {list(AccountInfo.__members__.keys())}")

    @field_validator("include")
    @classmethod
    def include_validator(cls, v):
        if v not in Include.__members__:
            raise ValueError(f"'{v}' is not a include. Must be one of {list(Include.__members__.keys())}")


@app.get("/")
def read_root():
    return {"message": "PennySpi API"}

@app.post("/scrape")
async def scrape_transactions(request: DownloadRequest):
    logger.info(request)
    software = Software[request.software]
    account_info = AccountInfo[request.account_info]
    include = Include[request.include]
    logger.info("Got scrape request for RBC with software : %s, account info : %s, include : %s", software, account_info, include)
    bank = RBCBank()
    bank.get_session_cookies()
    try:
        transaction_file = bank.download_transactions(software=software, account_info=account_info, include=include)
    except Exception as e:
        raise HTTPException(status_code=404, detail=e)
    if not transaction_file.exists():
        raise HTTPException(status_code=404, detail="Transaction file is not found")
    return FileResponse(path=transaction_file, filename=request.filename, media_type="application/octet-stream")

def run():
    uvicorn.run("pennyspy.scrapers.rbc_bank.rbc_api:app", host="0.0.0.0", port=API_PORT)