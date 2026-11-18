"""Running a skill or screener creates a new session seeded with a first prompt.

The response includes the prompt; the UI opens the session and sends it, so the
results stream into the chat like any other question.
"""
from string import Formatter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.api.sessions import check_model
from app.llm.providers import model_available
from app.models import ChatSession, Message, Screener, Skill, Tool
from app.screeners.engine import results_to_markdown, run_ibkr_scanner, run_screener
from app.tools.ibkr_gateway import LOCATIONS, SCAN_CODES, GatewayError

router = APIRouter(tags=["launch"])


class SkillRun(BaseModel):
    params: dict[str, str] = {}
    lead_model_tool_id: int | None = None  # this run's model, instead of the skill's


def render(template: str, params: dict[str, str], defaults: list[dict[str, Any]]) -> str:
    values = {p["name"]: p.get("default", "") for p in defaults} | {k: v for k, v in params.items() if v}
    missing = [f for _, f, _, _ in Formatter().parse(template) if f and not values.get(f)]
    if missing:
        raise HTTPException(422, f"Missing parameters: {', '.join(missing)}")
    # Your entries are shown in bold in the session's first message.
    return template.format(**{k: f"**{str(v).replace('*', '').strip()}**" for k, v in values.items()})


def data_notes(db: Session, tool_ids: list[int]) -> str:
    """A 'Data notes' callout listing what the referenced sources support and their limits."""
    if not tool_ids:
        return ""
    tools = db.exec(select(Tool).where(Tool.id.in_(tool_ids))).all()
    lines = [f"> - **{t.name}:** {t.notes.strip()}" for t in sorted(tools, key=lambda t: tool_ids.index(t.id))
             if t.notes.strip()]
    return ("\n\n> **Data notes**\n" + "\n".join(lines)) if lines else ""


def describe_rules(rule: dict[str, Any]) -> str:
    """Scanner rules in words, e.g. 'close > sma_200 AND rsi_14 < 40'."""
    if not rule:
        return ""
    if "all" in rule:
        parts = [describe_rules(r) for r in rule["all"] if r]
        return " AND ".join(f"({p})" if " OR " in p else p for p in parts)
    if "any" in rule:
        return " OR ".join(describe_rules(r) for r in rule["any"] if r)
    if "not" in rule:
        return f"NOT ({describe_rules(rule['not'])})"
    right = rule.get("field2") if "field2" in rule else rule.get("value")
    if rule.get("op") == "between" and isinstance(right, list) and len(right) == 2:
        return f"{rule.get('field')} between {right[0]} and {right[1]}"
    return f"{rule.get('field')} {rule.get('op')} {right}"


def _money(x: Any) -> str:
    return f"${float(x):,.0f}" if float(x) >= 100 else f"${float(x):,.2f}"


def run_model(db: Session, chosen: int | None, saved: int | None) -> int | None:
    """The model for a skill's or scanner's run: this run's choice (must be usable), else the saved
    one if its vendor key is set, else None (the Research default)."""
    if chosen:
        check_model(db, chosen)
        return chosen
    t = db.get(Tool, saved) if saved else None
    return saved if t is not None and t.kind == "model" and t.enabled and model_available(t.config) else None


@router.post("/skills/{skill_id}/run")
async def run_skill(skill_id: int, body: SkillRun, db: Session = Depends(get_session)) -> dict[str, Any]:
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise HTTPException(404)
    prompt = render(skill.prompt_template, body.params, skill.params)
    label = " ".join(v for v in body.params.values() if v)
    model_id = run_model(db, body.lead_model_tool_id, skill.lead_model_tool_id)
    scr = db.get(Screener, skill.screener_id) if skill.screener_id else None
    if skill.screener_id and scr is None:
        raise HTTPException(422, "This skill's scanner was deleted. Pick another scanner or none in Skills.")
    notes = data_notes(db, skill.tool_ids)
    if scr is None:
        # The skill's model answers the skill's prompt; follow-up questions use the Research default.
        chat = ChatSession(title=f"{skill.name}: {label}".strip(": "), origin=f"skill:{skill.id}",
                           next_turn_model_tool_id=model_id,
                           tool_scope=list(skill.tool_ids) or None)  # the skill's data sources, if it names any
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return {"session_id": chat.id, "prompt": prompt + notes}

    # Built on a scanner: scan, the scanner's analysis (scanner's model), then the skill's
    # evaluation (skill's model). Follow-up questions use the Research default.
    settings, results, scan_label, rows = await scan(scr, dict(scr.params), list(scr.universe))
    scanner_model = run_model(db, None, scr.lead_model_tool_id)
    chat = ChatSession(title=f"{skill.name}: {label}".strip(": ") + f" ({len(rows)} results)", origin=f"skill:{skill.id}",
                       tool_scope=list(skill.tool_ids) or None)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    models = {i: db.get(Tool, i).name for i in (scanner_model, model_id) if i}
    stages = [f"Scanner analysis: **{models.get(scanner_model, 'Research default')}**"] if scr.follow_up_prompt.strip() else []
    stages.append(f"Skill evaluation: **{models.get(model_id, 'Research default')}**")
    instructions = (prompt + f"\n\n**Using scanner {scr.name}**\n" + "\n".join(f"- {s}" for s in settings)
                    + "\n" + "\n".join(f"- {s}" for s in stages))
    add_exchange(db, chat.id, instructions, results, scan_label)
    evaluate = (f"{prompt}\n\nBase this on the scanner results above"
                f"{' and the scanner analysis' if scr.follow_up_prompt.strip() else ''}.{notes}")
    if scr.follow_up_prompt.strip():
        chat.next_turn_model_tool_id = scanner_model
        chat.queued_turns = [{"text": evaluate, "model_tool_id": model_id, "hidden": True, "label": skill.name}]
        first = f"Analyse the scanner results above and complete this task: {scr.follow_up_prompt}"
    else:  # the scanner has no task of its own: the skill evaluates the raw results
        chat.next_turn_model_tool_id = model_id
        first = evaluate
    db.add(chat)
    db.commit()
    return {"session_id": chat.id, "prompt": first, "hidden": True, "matches": rows}


class ScannerRun(BaseModel):
    """One-off settings for this run only; the saved scanner isn't changed."""
    params: dict[str, Any] = {}           # IBKR: rows, above_price, above_volume, market_cap_above
    universe: list[str] | None = None     # rule-based: tickers to check
    lead_model_tool_id: int | None = None


RUN_PARAM_KEYS = {"rows", "above_price", "above_volume", "market_cap_above", "average_option_volume_above"}


@router.get("/scanner-options")
def scanner_options() -> dict[str, Any]:
    """IBKR scan types and locations offered in the Scanners editor."""
    return {"scan_codes": [{"code": k, "label": v} for k, v in SCAN_CODES.items()],
            "locations": [{"code": k, "label": v} for k, v in LOCATIONS.items()]}


async def scan(scr: Screener, params: dict[str, Any], universe: list[str]) -> tuple[list[str], str, str, list[dict]]:
    """Run a scanner's scan (no LLM). Returns (settings lines, results text, label, rows)."""
    if scr.source == "ibkr":
        try:
            rows = await run_ibkr_scanner(params, scr.rules, scr.sort_by, scr.limit)
        except GatewayError as e:
            raise HTTPException(503, str(e)) from e
        except Exception as e:  # e.g. an invalid scan code
            raise HTTPException(502, f"IBKR scanner error: {e}") from e
        code = str(params.get("scan_code", ""))
        settings = [f"Scan: **{SCAN_CODES.get(code, code)}**",
                    f"Market: **{LOCATIONS.get(params.get('location', 'STK.US.MAJOR'), 'US stocks')}**",
                    f"Results requested: **{params.get('rows', 25)}**"]
        if params.get("above_price"):
            settings.append(f"Min price: **{_money(params['above_price'])}**")
        if params.get("above_volume"):
            settings.append(f"Min volume: **{int(params['above_volume']):,}**")
        if params.get("market_cap_above"):
            settings.append(f"Min market cap: **{_money(params['market_cap_above'])}M**")
        if scr.rules:
            settings.append(f"Extra rules: **{describe_rules(scr.rules)}**")
        outcome = f"IBKR returned {len(rows)} stocks{' after your rules' if scr.rules else ''}"
    else:
        if not universe:
            raise HTTPException(422, f"Scanner {scr.name} has no tickers to scan.")
        rows = await run_screener(universe, scr.rules, scr.sort_by, scr.limit)
        settings = [f"Tickers ({len(universe)}): **{', '.join(universe)}**"]
        if scr.rules:
            settings.append(f"Rules: **{describe_rules(scr.rules)}**")
        outcome = f"Found {len(rows)} matches out of {len(universe)} tickers"
    if scr.sort_by:
        settings.append(f"Sorted by: **{scr.sort_by}**")
    label = "IBKR scanner" if scr.source == "ibkr" else "Scanner"
    return settings, f"{outcome}:\n\n{results_to_markdown(rows)}", label, rows


def add_exchange(db: Session, chat_id: int, instructions: str, results: str, label: str) -> None:
    """The run reads like a normal exchange: instructions as the user's message, results as a response."""
    db.add(Message(session_id=chat_id, role="user", content=[{"type": "text", "text": instructions}]))
    db.add(Message(session_id=chat_id, role="assistant", model=label, content=[{"type": "text", "text": results}]))
    db.commit()


@router.post("/screeners/{screener_id}/run")
async def run_screen(screener_id: int, body: ScannerRun | None = None,
                     db: Session = Depends(get_session)) -> dict[str, Any]:
    scr = db.get(Screener, screener_id)
    if scr is None:
        raise HTTPException(404)
    body = body or ScannerRun()
    model_id = run_model(db, body.lead_model_tool_id, scr.lead_model_tool_id)
    params = {**scr.params, **{k: v for k, v in body.params.items() if k in RUN_PARAM_KEYS and v not in ("", None)}}
    universe = [t.strip().upper() for t in body.universe if t.strip()] if body.universe is not None else scr.universe
    if scr.source != "ibkr" and not universe:
        raise HTTPException(422, "Add at least one ticker to scan.")
    settings, results, label, rows = await scan(scr, params, universe)
    # The scanner's model analyses the results; follow-up questions use the Research default.
    chat = ChatSession(title=f"Scanner: {scr.name} ({len(rows)} results)", origin=f"screener:{scr.id}",
                       next_turn_model_tool_id=model_id)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    if model_id:
        settings.append(f"Analysis model: **{db.get(Tool, model_id).name}**")
    instructions = (f"Run scanner **{scr.name}**\n\n" + "\n".join(f"- {s}" for s in settings)
                    + f"\n\n**Task:** {scr.follow_up_prompt}")
    add_exchange(db, chat.id, instructions, results, label)
    # The UI sends this hidden prompt to start the Lead's analysis (no extra bubble is shown).
    return {"session_id": chat.id, "prompt": SCANNER_FOLLOW_UP, "hidden": True, "matches": rows}


SCANNER_FOLLOW_UP = "Analyse the scanner results above and complete the task."
