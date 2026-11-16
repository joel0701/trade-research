import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from app.api import launch, profile, sessions
from app.agents.orchestrator import mark_interrupted_runs
from app.api.crud import crud_router
from app.config import CORS_ORIGINS
from app.db import engine, init_db
from app.models import Role, Screener, Skill, Tool
from app.seed import seed
from app.llm.providers import PROVIDERS, provider_of
from app.tools.python_tools import PYTHON_TOOLS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with Session(engine) as db:
        seed(db)
    mark_interrupted_runs()
    yield


app = FastAPI(title="Trade Research", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

for model, prefix in [(Tool, "/tools"), (Role, "/roles"), (Skill, "/skills"), (Screener, "/screeners")]:
    app.include_router(crud_router(model, prefix), prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(launch.router, prefix="/api")
app.include_router(profile.router, prefix="/api")


@app.get("/api/models")
def models() -> list[dict]:
    """Model tools with whether their vendor's API key is configured (keys are never returned)."""
    with Session(engine) as db:
        out = []
        for t in db.exec(select(Tool).where(Tool.kind == "model").order_by(Tool.id)).all():
            try:
                p = provider_of(t.config)
                info = {"provider": p.slug, "provider_name": p.name, "key_env": p.key_env, "available": p.available()}
            except ValueError as e:
                info = {"provider": t.config.get("provider"), "provider_name": "?", "key_env": None,
                        "available": False, "problem": str(e)}
            out.append({**t.model_dump(), **info, "available": info["available"] and t.enabled})
        return out


@app.get("/api/providers")
def providers() -> list[dict]:
    """LLM vendors and whether each has a key in .env."""
    return [{"slug": p.slug, "name": p.name, "key_env": p.key_env, "configured": p.available(),
             "api": p.api, "base_url": p.url()} for p in PROVIDERS.values()]


@app.get("/api/python-functions")
def python_functions() -> list[dict[str, str]]:
    """Built-in functions available for tools of kind 'python'."""
    return [{"name": k, "description": v.description} for k, v in PYTHON_TOOLS.items()]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
