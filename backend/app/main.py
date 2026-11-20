import logging
from typing import Any
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from app.api import launch, local_models, profile, sessions
from app.agents.orchestrator import mark_interrupted_runs
from app.api.crud import crud_router
from app.config import CORS_ORIGINS
from app.db import engine, init_db
from app.catalog import CAPABILITIES
from app.models import Connection, Role, Screener, Skill, Tool
from app.tools.registry import availability
from app.seed import seed
from app.llm.providers import PROVIDERS, local_model_states, provider_of
from app.tools.python_tools import PYTHON_TOOLS

from app.logs import setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()  # again here: uvicorn sets up its own loggers after importing the app
    init_db()
    with Session(engine) as db:
        seed(db)
    mark_interrupted_runs()
    yield


app = FastAPI(title="Trade Research", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

for model, prefix in [(Tool, "/tools"), (Connection, "/connections"), (Role, "/team"), (Skill, "/skills"),
                      (Screener, "/screeners")]:
    app.include_router(crud_router(model, prefix), prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(launch.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(local_models.router, prefix="/api")


class ClientLog(BaseModel):
    level: str = "error"
    message: str
    detail: str = ""
    url: str = ""


_frontend_log = logging.getLogger("frontend")


@app.post("/api/client-log", status_code=204)
def client_log(entry: ClientLog) -> None:
    """Errors reported by the browser, written to data/logs/frontend.log."""
    level = logging.WARNING if entry.level == "warning" else logging.ERROR
    text = entry.message[:2000] + (f" | {entry.url[:300]}" if entry.url else "")
    if entry.detail:
        text += "\n" + entry.detail[:6000]
    _frontend_log.log(level, text)


@app.get("/api/models")
def models() -> list[dict]:
    """Model tools with whether their vendor's API key is configured (keys are never returned)."""
    with Session(engine) as db:
        out = []
        for t in db.exec(select(Tool).where(Tool.kind == "model").order_by(Tool.id)).all():
            try:
                p = provider_of(t.config)
                info = {"provider": p.slug, "provider_name": p.name, "key_env": p.key_env, "available": p.available(),
                        "local": p.local, "base_url": p.url() if p.local else None}
                if p.local and info["available"]:
                    st = local_model_states(p).get(t.config.get("model_id"), {})
                    info["loaded"] = st.get("loaded")  # None = the server doesn't say
                    info["context"] = st.get("context")
            except ValueError as e:
                info = {"provider": t.config.get("provider"), "provider_name": "?", "key_env": None,
                        "available": False, "problem": str(e)}
            out.append({**t.model_dump(), **info, "available": info["available"] and t.enabled})
        return out


@app.get("/api/capabilities")
def capabilities() -> list[dict]:
    """Data categories, in display order."""
    return [{"slug": k, **v} for k, v in CAPABILITIES.items()]


@app.get("/api/tools-status")
def tools_status() -> list[dict]:
    """Whether each data source can be used right now, and why not (e.g. a missing key)."""
    with Session(engine) as db:
        conns = {c.id: c for c in db.exec(select(Connection)).all()}
        out = []
        for t in db.exec(select(Tool).where(Tool.kind != "model").order_by(Tool.id)).all():
            ok, why = availability(t, conns.get(t.connection_id) if t.connection_id else None)
            conn = conns.get(t.connection_id) if t.connection_id else None
            out.append({"id": t.id, "available": ok, "reason": why, "pricing": conn.pricing if conn else ""})
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
def health() -> dict[str, Any]:
    from app.llm.mock_loop import mock_mode

    return {"status": "ok", "mock_llm": mock_mode()}
