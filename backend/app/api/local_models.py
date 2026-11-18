"""Local models: find the models installed on local servers (Ollama, LM Studio, any OpenAI-compatible
server) and add them as models in Tools. They cost nothing, so prices are 0."""
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.llm.providers import PROVIDERS, server_up
from app.models import Tool

router = APIRouter()
LOCAL = [p for p in PROVIDERS.values() if p.local]


def _installed(base_url: str) -> list[str]:
    try:
        data = httpx.get(base_url.rstrip("/") + "/models", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return []
    return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))


@router.get("/local-models")
def local_models(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Each local server: where it's expected, whether it's running, and its installed models."""
    added = {(t.config.get("provider"), t.config.get("model_id")): t.id
             for t in db.exec(select(Tool).where(Tool.kind == "model")).all()}
    out = []
    for p in LOCAL:
        url = p.url()
        running = bool(url) and server_up(url, ttl=0)
        models = _installed(url) if running else []
        out.append({"provider": p.slug, "name": p.name, "base_url": url, "base_url_env": p.base_url_env,
                    "running": running,
                    "models": [{"model_id": m, "tool_id": added.get((p.slug, m))} for m in models]})
    return out


class AddLocal(BaseModel):
    provider: str
    model_id: str


@router.post("/local-models")
def add_local_model(body: AddLocal, db: Session = Depends(get_session)) -> dict[str, Any]:
    p = PROVIDERS.get(body.provider)
    if p is None or not p.local:
        raise HTTPException(422, "Not a local provider.")
    existing = next((t for t in db.exec(select(Tool).where(Tool.kind == "model")).all()
                     if t.config.get("provider") == p.slug and t.config.get("model_id") == body.model_id), None)
    if existing:
        return existing.model_dump()
    base = re.sub(r"[^a-z0-9]+", "_", f"{p.slug}_{body.model_id}".lower()).strip("_")[:60]
    taken = {t.slug for t in db.exec(select(Tool)).all()}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    tool = Tool(slug=slug, name=f"{body.model_id} ({p.name.split(' (')[0]})", kind="model",
                description=f"Local model served by {p.name}. Free; speed and quality depend on your machine.",
                config={"provider": p.slug, "model_id": body.model_id, "max_tokens": 8000,
                        "price_input": 0, "price_output": 0})
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool.model_dump()
