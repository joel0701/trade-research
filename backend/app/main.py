import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.api import launch, sessions
from app.api.crud import crud_router
from app.config import CORS_ORIGINS
from app.db import engine, init_db
from app.models import Role, Screener, Skill, Tool
from app.seed import seed
from app.tools.python_tools import PYTHON_TOOLS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with Session(engine) as db:
        seed(db)
    yield


app = FastAPI(title="Trade Research", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

for model, prefix in [(Tool, "/tools"), (Role, "/roles"), (Skill, "/skills"), (Screener, "/screeners")]:
    app.include_router(crud_router(model, prefix), prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(launch.router, prefix="/api")


@app.get("/api/python-functions")
def python_functions() -> list[dict[str, str]]:
    """Built-in functions available for tools of kind 'python'."""
    return [{"name": k, "description": v.description} for k, v in PYTHON_TOOLS.items()]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
