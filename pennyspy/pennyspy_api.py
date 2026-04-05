import os
import pathlib
from typing import Final

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pennyspy.scrapers.rbc_bank import rbc_api
from pennyspy.scrapers.wealthsimple import ws_api

from logging import getLogger

logger = getLogger(__name__)

app = FastAPI()
app.include_router(rbc_api.router, prefix="/rbc", tags=["RBC"])
app.include_router(ws_api.router, prefix="/ws", tags=["Wealthsimple"])

API_PORT: Final[int] = os.getenv("PENNYSPY_PORT", 5056)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = pathlib.Path(
    os.getenv("FRONTEND_DIR", pathlib.Path(__file__).parent.parent / "frontend")
)
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.get("/", include_in_schema=False)
def read_root():
    return RedirectResponse(url="/app/index.html")


def run():
    uvicorn.run("pennyspy.pennyspy_api:app", host="0.0.0.0", port=API_PORT)
