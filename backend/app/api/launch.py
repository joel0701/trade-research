"""Running a skill or screener creates a new session seeded with a first prompt.

The response includes the prompt; the UI opens the session and sends it, so the
results stream into the chat like any other question.
"""
from datetime import datetime, time as dtime
from string import Formatter
from typing import Any
from zoneinfo import ZoneInfo

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
                           next_turn_tool_scope=list(skill.tool_ids) or None)  # for the skill's run only
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return {"session_id": chat.id, "prompt": prompt + notes}

    # Built on a scanner: the scan (no LLM), then the skill's prompt evaluates the results with the
    # skill's model. Follow-up questions use the Research default.
    universe, lines, note = ([], [], None) if scr.source == "ibkr" else await _resolve_off_loop(scr.id)
    settings, results, scan_label, rows = await scan(scr, dict(scr.params), universe, lines, note)
    chat = ChatSession(title=f"{skill.name}: {label}".strip(": ") + f" ({len(rows)} results)", origin=f"skill:{skill.id}",
                       next_turn_model_tool_id=model_id,
                       next_turn_tool_scope=list(skill.tool_ids) or None)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    model_name = db.get(Tool, model_id).name if model_id else "Research default"
    instructions = (prompt + f"\n\n**Using scanner {scr.name}**\n" + "\n".join(f"- {s}" for s in settings)
                    + f"\n- Skill evaluation: **{model_name}**")
    add_exchange(db, chat.id, instructions, results, scan_label)
    evaluate = f"{prompt}\n\nBase this on the scanner results above." + SCAN_SCOPE + notes
    return {"session_id": chat.id, "prompt": evaluate, "hidden": True, "matches": rows}


class ScannerRun(BaseModel):
    """One-off settings for this run only; the saved scanner isn't changed."""
    params: dict[str, Any] = {}           # IBKR: rows, above_price, above_volume, market_cap_above
    universe: list[str] | None = None     # rule-based: tickers to check (extra tickers when an index is chosen)
    universe_index: str | None = None     # rule-based: "sp500", "nasdaq100", "dow30"; "" = custom list only


def resolve_universe(db: Session, scr: Screener, tickers: list[str] | None = None,
                     index: str | None = None, index_set: bool = False) -> tuple[list[str], list[str], str | None]:
    """(tickers to scan, settings lines describing them, note). The scanner's saved stock list and
    tickers, unless this run overrides them. A list due for refresh is refreshed first."""
    from app.screeners.universes import UniverseError, get_list

    extra = [t.strip().upper() for t in (tickers if tickers is not None else scr.universe) if t and t.strip()]
    key = (index or None) if index_set else scr.universe_index
    if not key:
        shown = ", ".join(extra) if len(extra) <= 30 else ", ".join(extra[:30]) + f", … (+{len(extra) - 30} more)"
        return extra, [f"Tickers ({len(extra)}): **{shown}**"], None
    try:
        sl, note = get_list(db, key)
    except UniverseError as e:
        raise HTTPException(503, str(e)) from e
    members = list(sl.tickers)
    combined = list(dict.fromkeys(members + extra))
    as_of = f", as of {sl.refreshed_at.date().isoformat()}" if sl.refreshed_at else ""
    lines = [f"Universe: **{sl.name}** ({len(members)} tickers{as_of})"]
    added = [t for t in extra if t not in members]
    if added:
        lines.append(f"Extra tickers: **{', '.join(added[:30])}**" + (f" (+{len(added) - 30} more)" if len(added) > 30 else ""))
    return combined, lines, note


async def _resolve_off_loop(screener_id: int, tickers: list[str] | None = None, index: str | None = None,
                            index_set: bool = False) -> tuple[list[str], list[str], str | None]:
    """resolve_universe on a worker thread with its own session: refreshing a stock list can take a
    while (downloads, maybe a model call), and the server must keep answering other requests."""
    import asyncio

    from app.db import engine

    def work():
        with Session(engine) as db2:
            return resolve_universe(db2, db2.get(Screener, screener_id), tickers, index, index_set)
    return await asyncio.to_thread(work)


@router.get("/universes")
def universes(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Stock lists a rule-based scanner can scan."""
    from app.screeners.universes import list_info

    return list_info(db)


RUN_PARAM_KEYS = {"rows", "above_price", "above_volume", "market_cap_above", "average_option_volume_above"}


@router.get("/scanner-options")
def scanner_options() -> dict[str, Any]:
    """IBKR scan types and locations offered in the Scanners editor."""
    return {"scan_codes": [{"code": k, "label": v} for k, v in SCAN_CODES.items()],
            "locations": [{"code": k, "label": v} for k, v in LOCATIONS.items()]}


async def scan(scr: Screener, params: dict[str, Any], universe: list[str],
               universe_lines: list[str] | None = None, note: str | None = None) -> tuple[list[str], str, str, list[dict]]:
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
        session, session_note = market_session(now_et())
        settings.append(f"Market session: **{session}**" + (f" ({session_note})" if session_note else ""))
        requested = int(params.get("rows", 25) or 25)
        warnings = []
        if not scr.rules and len(rows) < max(3, requested // 2):
            warnings.append(f"⚠ Only {len(rows)} of {requested} requested results"
                            + (" (normal outside regular trading hours)." if session != "open" else "."))
        warnings.append("Indicator columns (pct_change_1d, rsi_14, moving averages…) are from Yahoo's daily bars as "
                        "of the last close; IBKR's ranking uses live or extended-hours prices, so they can disagree.")
    else:
        if not universe:
            raise HTTPException(422, f"Scanner {scr.name} has no tickers to scan.")
        rows = await run_screener(universe, scr.rules, scr.sort_by, scr.limit)
        settings = list(universe_lines or [f"Tickers ({len(universe)}): **{', '.join(universe)}**"])
        if scr.rules:
            settings.append(f"Rules: **{describe_rules(scr.rules)}**")
        outcome = f"Found {len(rows)} matches out of {len(universe)} tickers"
        if len(rows) >= scr.limit:
            outcome += f" (showing the first {scr.limit}; raise Max results to see more)"
        if note:
            outcome = f"_{note}_\n\n{outcome}"
    if scr.sort_by:
        settings.append(f"Sorted by: **{scr.sort_by}**")
    label = "IBKR scanner" if scr.source == "ibkr" else "Scanner"
    notes = "\n\n" + "\n\n".join(f"_{w}_" for w in warnings) if scr.source == "ibkr" else ""
    return settings, f"{outcome}:\n\n{results_to_markdown(rows)}{notes}", label, rows


def add_exchange(db: Session, chat_id: int, instructions: str, results: str, label: str) -> None:
    """The run reads like a normal exchange: instructions as the user's message, results as a response."""
    db.add(Message(session_id=chat_id, role="user", content=[{"type": "text", "text": instructions}]))
    db.add(Message(session_id=chat_id, role="assistant", model=label, content=[{"type": "text", "text": results}]))
    db.commit()


def scan_suggestions(rows: list[dict[str, Any]]) -> str:
    """Free follow-up questions about the top results (no LLM): shown as suggestions in the UI."""
    tickers = [str(r["ticker"]) for r in rows[:3] if r.get("ticker")]
    if not tickers:
        return ""
    names = tickers[0] if len(tickers) == 1 else ", ".join(tickers[:-1]) + " and " + tickers[-1]
    them = "it" if len(tickers) == 1 else "these"
    return (f"\n\n{NEXT_STEPS_HEADING}\n"
            f"- What news is driving today's moves in {names}?\n"
            f"- What's the technical setup for {names}, and do the moves look like they can continue?\n"
            f"- Are there options setups worth a look in {names}? Compare risk/reward for {them}.")


@router.post("/screeners/{screener_id}/run")
async def run_screen(screener_id: int, body: ScannerRun | None = None,
                     db: Session = Depends(get_session)) -> dict[str, Any]:
    """Run the scan and show the results. No LLM is involved: you then ask about the results, and
    those questions use the Research default model and the full team."""
    scr = db.get(Screener, screener_id)
    if scr is None:
        raise HTTPException(404)
    body = body or ScannerRun()
    params = {**scr.params, **{k: v for k, v in body.params.items() if k in RUN_PARAM_KEYS and v not in ("", None)}}
    universe, lines, note = ([], [], None) if scr.source == "ibkr" else await _resolve_off_loop(
        scr.id, body.universe, body.universe_index, "universe_index" in body.model_fields_set)
    if scr.source != "ibkr" and not universe:
        raise HTTPException(422, "Add at least one ticker to scan, or choose an index.")
    settings, results, label, rows = await scan(scr, params, universe, lines, note)
    chat = ChatSession(title=f"Scanner: {scr.name} ({len(rows)} results)", origin=f"screener:{scr.id}")
    db.add(chat)
    db.commit()
    db.refresh(chat)
    instructions = f"Run scanner **{scr.name}**\n\n" + "\n".join(f"- {s}" for s in settings)
    add_exchange(db, chat.id, instructions, results + scan_suggestions(rows), label)
    return {"session_id": chat.id, "matches": rows}


from app.agents.orchestrator import NEXT_STEPS_HEADING  # noqa: E402  (every Lead answer ends with suggestions)

# Keeps a scanner's analysis on the scanned stocks (a model shouldn't swap in its own list when the
# scan looks odd; the user can ask for more as a follow-up).
SCAN_SCOPE = (" Work only with the stocks in the scanner results: don't research, add or recommend other tickers. "
              "If the results look incomplete or inconsistent with the scan (few rows, a market that's closed, "
              "'gainers' that are down), say so first and suggest when to re-run, then analyse what's there.")

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(ET)


def market_session(now: datetime) -> tuple[str, str]:
    """(session, note) for US stocks at `now` (weekends; exchange holidays aren't known here)."""
    t = now.astimezone(ET)
    if t.weekday() >= 5:
        return "closed (weekend)", "IBKR ranks on the last session's data; results are often few or stale."
    if dtime(9, 30) <= t.time() < dtime(16, 0):
        return "open", ""
    if dtime(4, 0) <= t.time() < dtime(9, 30):
        return "pre-market", "IBKR ranks on thin pre-market trading; expect few results and big swings."
    if dtime(16, 0) <= t.time() < dtime(20, 0):
        return "after hours", "IBKR ranks on thin after-hours trading; expect few results."
    return "closed", "Outside trading hours IBKR's ranking is often stale or nearly empty; re-run when the market is open."
