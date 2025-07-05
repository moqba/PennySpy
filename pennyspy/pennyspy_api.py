from typing import Final

import uvicorn
from fastapi import FastAPI
from pennyspy.scrapers.rbc_bank import rbc_api

from pennyspy.scrapers.get_required_env_var import get_required_env_var

from logging import getLogger
logger = getLogger(__name__)

app = FastAPI()
app.include_router(rbc_api.router, prefix="/rbc", tags=["RBC"])

API_PORT: Final[int] = int(get_required_env_var("PENNYSPY_PORT"))

@app.get("/")
def read_root():
    return {"message": "PennySpy API"}

def run():
    uvicorn.run("pennyspy.pennyspy_api:app", host="0.0.0.0", port=API_PORT)
