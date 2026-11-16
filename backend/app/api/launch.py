"""Running a skill or screener creates a new session seeded with a first prompt.

The response includes the prompt; the UI opens the session and sends it, so the
results stream into the chat like any other question.
"""
from string import Formatter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db import get_session
from app.models import ChatSession, Screener, Skill, Tool
from app.screeners.engine import results_to_markdown, run_screener

router = APIRouter(tags=["launch"])


class SkillRun(BaseModel):
    params: dict[str, str] = {}
    lead_model_tool_id: int | None = None  # overrides the skill's default Lead model


def render(template: str, params: dict[str, str], defaults: list[dict[str, Any]]) -> str:
    values = {p["name"]: p.get("default", "") for p in defaults} | {k: v for k, v in params.items() if v}
    missing = [f for _, f, _, _ in Formatter().parse(template) if f and not values.get(f)]
    if missing:
        raise HTTPException(422, f"Missing parameters: {', '.join(missing)}")
    return template.format(**values)


@router.post("/skills/{skill_id}/run")
def run_skill(skill_id: int, body: SkillRun, db: Session = Depends(get_session)) -> dict[str, Any]:
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(404)
    prompt = render(skill.prompt_template, body.params, skill.params)
    label = " ".join(v for v in body.params.values() if v)
    model_id = body.lead_model_tool_id or skill.lead_model_tool_id
    model = db.get(Tool, model_id) if model_id else None
    if model_id and (model is None or model.kind != "model"):
        raise HTTPException(422, "lead_model_tool_id must refer to a model in Tools")
    chat = ChatSession(title=f"{skill.name}: {label}".strip(": "), origin=f"skill:{skill.id}",
                       lead_model_tool_id=model_id)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return {"session_id": chat.id, "prompt": prompt}


@router.post("/screeners/{screener_id}/run")
async def run_screen(screener_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    scr = db.get(Screener, screener_id)
    if scr is None:
        raise HTTPException(404)
    rows = await run_screener(scr.universe, scr.rules, scr.sort_by, scr.limit)
    table = results_to_markdown(rows)
    chat = ChatSession(title=f"Screener: {scr.name} ({len(rows)} matches)", origin=f"screener:{scr.id}")
    db.add(chat)
    db.commit()
    db.refresh(chat)
    # Results go in the first user prompt so the Lead sees exactly what the user sees.
    prompt = (f"Screener **{scr.name}** ({scr.description}) scanned {len(scr.universe)} tickers "
              f"and found {len(rows)} matches:\n\n{table}\n\n{scr.follow_up_prompt}")
    return {"session_id": chat.id, "prompt": prompt, "matches": rows}
