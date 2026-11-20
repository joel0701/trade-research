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
from app.models import Connection, Role, Screener, Skill, StockList, Tool
from app.tools.registry import availability
from app.seed import seed
from app.llm.providers import PROVIDERS, local_model_states, provider_of
from app.llm.tiers import is_tier, resolve, target_tool
from app.tools.python_tools import PYTHON_TOOLS

from app.logs import setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()  # again here: uvicorn sets up its own loggers after importing the app
    init_db()
    with Session(engine) as db:
        seed(db)
        from app.api.profile import get_profile
        from app.llm.mock_loop import set_mock

        prof = get_profile(db)
        set_mock(prof.mock_llm)  # the profile's Test mode toggle
        from app.llm.tiers import set_targets
        set_targets(prof.model_low_id, prof.model_mid_id, prof.model_high_id)  # default models
    mark_interrupted_runs()
    yield


app = FastAPI(title="Trade Research", lifespan=lifespan)


@app.exception_handler(Exception)
async def unexpected_error(request, exc: Exception):
    """Any unexpected error comes back as a readable message (not a plain-text 500, which the UI would
    mistake for the backend being down), with the full traceback in the backend log."""
    from fastapi.responses import JSONResponse

    logging.getLogger("app.errors").exception("%s %s failed", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Something went wrong: {type(exc).__name__}: {exc}. "
                                                            "Details are in data/logs/backend-latest.log."})
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

for model, prefix in [(Tool, "/tools"), (Connection, "/connections"), (Role, "/team"), (Skill, "/skills"),
                      (Screener, "/screeners"), (StockList, "/stock-lists")]:
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


@app.post("/api/stock-lists/{list_id}/refresh")
def refresh_stock_list(list_id: int) -> dict[str, Any]:
    """Refresh a stock list now (the "Refresh now" button)."""
    from fastapi import HTTPException

    from app.screeners.universes import UniverseError, refresh

    with Session(engine) as db:
        sl = db.get(StockList, list_id)
        if sl is None:
            raise HTTPException(404)
        try:
            return refresh(db, sl).model_dump(mode="json")
        except UniverseError as e:
            raise HTTPException(502, str(e)) from e


@app.post("/api/stock-lists/{list_id}/find-source")
async def find_stock_list_source(list_id: int) -> dict[str, Any]:
    """Find the ETF's holdings file (main source) and a Wikipedia table (backup), verify them, save them."""
    from fastapi import HTTPException

    from app.models import now
    from app.screeners.discover import find_sources
    from app.screeners.universes import UniverseError

    with Session(engine) as db:
        sl = db.get(StockList, list_id)
        if sl is None:
            raise HTTPException(404)
        try:
            import asyncio
            found = await asyncio.wait_for(find_sources(db, sl.symbol, sl.name, sl.min_count), timeout=180)
        except asyncio.TimeoutError as e:
            raise HTTPException(504, "Find source took longer than 3 minutes and was stopped. The sites may be "
                                     "slow; try again, or enter the address yourself.") from e
        except UniverseError as e:
            raise HTTPException(422, str(e)) from e
        day, notes = now().date().isoformat(), []
        main, backup = found["main"], found["backup"]
        if main:
            sl.url, sl.column = main["url"], main["column"]
            notes.append(f"ETF holdings found by {main['found_by']}")
        elif found["main_error"]:
            notes.append(f"no ETF holdings file found ({found['main_error']})")
        if backup:
            sl.fallback_url, sl.fallback_column = backup["url"], backup["column"]
            notes.append(f"backup found by {backup['found_by']}")
        best = main or backup
        sl.source, sl.tickers, sl.refreshed_at, sl.last_error = "web", best["tickers"], now(), None
        sl.last_source = "main" if main else "backup"
        if not main:  # only a backup: keep it as the source used
            sl.url, sl.column = sl.url or "", sl.column
        text = "; ".join(notes)
        sl.source_note = text[:1].upper() + text[1:] + f" ({day})"
        sl.source_checked_at = now()
        if sl.refresh == "manual":
            sl.refresh = "weekly"
        db.add(sl)
        db.commit()
        db.refresh(sl)
        return sl.model_dump(mode="json")


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
                if is_tier(t.config):  # a default: named and grouped as such, pointing at a real model
                    target = target_tool(t.config.get("tier", "mid"))
                    info.update(provider_name="Defaults (set in Settings)", tier=t.config.get("tier"),
                                target_id=target.id if target else None, target_name=target.name if target else None)
                if p.local and info["available"]:
                    st = local_model_states(p).get(resolve(t.config).get("model_id"), {})
                    info["loaded"] = st.get("loaded")  # None = the server doesn't say
                    info["context"] = st.get("context")
            except ValueError as e:
                info = {"provider": t.config.get("provider"), "provider_name": "?", "key_env": None,
                        "available": False, "problem": str(e)}
            row = {**t.model_dump(), **info, "available": info["available"] and t.enabled}
            if info.get("tier"):
                row["name"] = f"{t.name} ({info['target_name'] or 'not set'})"
            out.append(row)
        out.sort(key=lambda m: (not m.get("tier"), 0))  # defaults first, then the rest in order
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

    from app.llm.mock_loop import mock_env

    return {"status": "ok", "mock_llm": mock_mode(), "mock_llm_env": mock_env()}
