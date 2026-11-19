"""Local models: find the models installed on local servers (Ollama, LM Studio, any OpenAI-compatible
server) and add them as models in Tools. They cost nothing, so prices are 0."""
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.llm.providers import MIN_CONTEXT, PROVIDERS, local_model_states, server_up
from app.models import Tool

router = APIRouter()
EMBEDDING = re.compile(r"embed|bge-|e5-|minilm|rerank", re.I)
LOCAL = [p for p in PROVIDERS.values() if p.local]


def _installed(base_url: str) -> list[str]:
    try:
        data = httpx.get(base_url.rstrip("/") + "/models", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return []
    # Embedding models can't chat, so they're not offered.
    return sorted(m["id"] for m in data.get("data", []) if m.get("id") and not EMBEDDING.search(m["id"]))


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
        states = local_model_states(p, ttl=0) if running else {}
        rows = []
        for m in models:
            st = states.get(m, {})
            if st.get("chat") is False:
                continue  # an embedding model (by the server's own type)
            ctx = st.get("context")
            rows.append({"model_id": m, "tool_id": added.get((p.slug, m)), "loaded": st.get("loaded"),
                         "context": ctx, "context_low": bool(ctx and ctx < MIN_CONTEXT)})
        rows.sort(key=lambda r: (r["loaded"] is False, r["model_id"]))  # loaded models first
        out.append({"provider": p.slug, "name": p.name, "base_url": url, "base_url_env": p.base_url_env,
                    "running": running, "min_context": MIN_CONTEXT, "models": rows})
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
