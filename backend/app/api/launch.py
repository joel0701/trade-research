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
from app.api.sessions import check_model
from app.llm.providers import model_available
from app.models import ChatSession, Screener, Skill, Tool
from app.screeners.engine import results_to_markdown, run_ibkr_scanner, run_screener
from app.tools.ibkr_gateway import LOCATIONS, SCAN_CODES, GatewayError

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
    if body.lead_model_tool_id:
        check_model(db, body.lead_model_tool_id)  # an explicit choice must be usable
    model_id = body.lead_model_tool_id or skill.lead_model_tool_id
    # A skill's saved default without a key is skipped; the Lead role's model is used instead.
    default = db.get(Tool, model_id) if model_id else None
    if default is None or default.kind != "model" or not model_available(default.config):
        model_id = None
    chat = ChatSession(title=f"{skill.name}: {label}".strip(": "), origin=f"skill:{skill.id}",
                       lead_model_tool_id=model_id)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return {"session_id": chat.id, "prompt": prompt}


@router.get("/scanner-options")
def scanner_options() -> dict[str, Any]:
    """IBKR scan types and locations offered in the Scanners editor."""
    return {"scan_codes": [{"code": k, "label": v} for k, v in SCAN_CODES.items()],
            "locations": [{"code": k, "label": v} for k, v in LOCATIONS.items()]}


@router.post("/screeners/{screener_id}/run")
async def run_screen(screener_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    scr = db.get(Screener, screener_id)
    if scr is None:
        raise HTTPException(404)
    if scr.source == "ibkr":
        try:
            rows = await run_ibkr_scanner(scr.params, scr.rules, scr.sort_by, scr.limit)
        except GatewayError as e:
            raise HTTPException(503, str(e)) from e
        except Exception as e:  # e.g. an invalid scan code
            raise HTTPException(502, f"IBKR scanner error: {e}") from e
        code = str(scr.params.get("scan_code", ""))
        what = (f"IBKR scanner **{scr.name}** ({SCAN_CODES.get(code, code)}, "
                f"{LOCATIONS.get(scr.params.get('location', 'STK.US.MAJOR'), 'US stocks')})"
                f"{' filtered by your rules' if scr.rules else ''} returned {len(rows)} stocks")
    else:
        rows = await run_screener(scr.universe, scr.rules, scr.sort_by, scr.limit)
        what = f"Scanner **{scr.name}** ({scr.description}) checked {len(scr.universe)} tickers and found {len(rows)} matches"
    table = results_to_markdown(rows)
    chat = ChatSession(title=f"Scanner: {scr.name} ({len(rows)} results)", origin=f"screener:{scr.id}")
    db.add(chat)
    db.commit()
    db.refresh(chat)
    # Results go in the first user prompt so the Lead sees exactly what the user sees.
    prompt = f"{what}:\n\n{table}\n\n{scr.follow_up_prompt}"
    return {"session_id": chat.id, "prompt": prompt, "matches": rows}
