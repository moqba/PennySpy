import os
from typing import Final

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pennyspy.scrapers.rbc_bank import rbc_api

from logging import getLogger
logger = getLogger(__name__)

app = FastAPI()
app.include_router(rbc_api.router, prefix="/rbc", tags=["RBC"])

API_PORT: Final[int] = os.getenv("PENNYSPY_PORT", 5056)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "PennySpy API"}

def run():
    uvicorn.run("pennyspy.pennyspy_api:app", host="0.0.0.0", port=API_PORT)
