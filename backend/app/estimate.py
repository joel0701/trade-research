"""Rough token and cost estimates for a follow-up question (shown on each suggested next step).

No model is called: the estimate comes from what earlier questions actually used.
1. Baseline: the median tokens and cost of your recent questions answered by the same Lead
   model, counting the Lead and every specialist it consulted. With no history for that model,
   the median across all models, priced for this one; with no history at all, a fixed guess.
2. Conversation: the Lead re-reads the whole conversation on each of its model calls, so this
   chat's current length is added once per typical call.
3. Scope: questions that name several tickers or ask to compare, screen or go through a list
   usually consult more specialists, so they're scaled up (at most 3x).
The answer is a range (half to double the estimate): real usage varies a lot with what the
team finds.
"""
import json
import re
import statistics
from typing import Any

from sqlmodel import Session, select

from app.models import ChatSession, Message, Role, Run, Tool

HISTORY = 30  # recent questions used for the baseline
FALLBACK = {"input": 60_000, "output": 4_000, "calls": 4}  # no history yet
BROAD = re.compile(r"\b(compare|comparison|versus|vs\.?|each|every|all of|rank|screen|scan|list|backtest|"
                   r"peers?|sector|portfolio|options? chain)\b", re.I)
NOT_TICKERS = {"A", "I", "AI", "US", "USA", "ETF", "ETFS", "EPS", "PE", "P/E", "CEO", "CFO", "IPO", "GDP", "CPI",
               "FED", "SEC", "RSI", "MACD", "SMA", "EMA", "ATR", "YOY", "QOQ", "TTM", "FY", "Q1", "Q2", "Q3", "Q4",
               "IV", "DCF", "ROE", "ROIC", "FCF", "NYSE", "OK", "AND", "OR", "THE", "VS", "IBKR", "ATH", "EV"}


def _prices(cfg: dict[str, Any]) -> tuple[float, float, float] | None:
    from app.llm.tiers import resolve

    cfg = resolve(cfg)
    if cfg.get("price_input") is None or cfg.get("price_output") is None:
        return None
    pin = float(cfg["price_input"])
    return pin, float(cfg["price_output"]), float(cfg.get("price_cache_read", pin))


def _run_tokens(r: Run) -> tuple[int, int]:
    u = r.usage or {}
    return (int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
            + int(u.get("cache_creation_input_tokens") or 0), int(u.get("output_tokens") or 0))


def _run_cost(r: Run, prices: dict[str, tuple[float, float, float]]) -> float | None:
    p = prices.get(r.model or "")
    if p is None:
        return None
    u = r.usage or {}
    return ((u.get("input_tokens") or 0) * p[0] + (u.get("cache_creation_input_tokens") or 0) * p[0] * 1.25
            + (u.get("cache_read_input_tokens") or 0) * p[2] + (u.get("output_tokens") or 0) * p[1]) / 1e6


def _history(db: Session, prices: dict[str, tuple[float, float, float]]) -> list[dict[str, Any]]:
    """Recent finished questions: {model, input, output, cost (or None), calls (Lead model calls)}."""
    leads = db.exec(select(Run).where(Run.parent_run_id == None, Run.status == "ok")  # noqa: E711
                    .order_by(Run.id.desc()).limit(HISTORY * 4)).all()
    leads = [r for r in leads if r.model and r.model != "mock" and any((r.usage or {}).values())]
    if not leads:
        return []
    kids: dict[int, list[Run]] = {}
    for k in db.exec(select(Run).where(Run.parent_run_id.in_([r.id for r in leads]))).all():  # type: ignore[union-attr]
        kids.setdefault(k.parent_run_id, []).append(k)
    out = []
    for r in leads:
        group = [r, *kids.get(r.id, [])]
        toks = [_run_tokens(x) for x in group]
        costs = [_run_cost(x, prices) for x in group]
        out.append({"model": r.model, "input": sum(t[0] for t in toks), "output": sum(t[1] for t in toks),
                    "cost": None if any(c is None for c in costs) else sum(costs),  # type: ignore[misc]
                    "calls": len(r.tool_calls or []) + 1})
    return out


def _conversation_tokens(db: Session, session_id: int) -> int:
    rows = db.exec(select(Message).where(Message.session_id == session_id, Message.role != "notice")).all()
    return sum(len(json.dumps(m.content)) for m in rows) // 4  # ~4 characters per token


def scope_factor(question: str) -> float:
    words = re.findall(r"\$?\b[A-Z]{1,5}\b", question)
    tickers = {w.lstrip("$") for w in words} - NOT_TICKERS
    f = 1 + 0.5 * max(0, len(tickers) - 1)
    if BROAD.search(question):
        f *= 1.3
    return min(f, 3.0)


def estimate(db: Session, session_id: int, questions: list[str]) -> dict[str, Any]:
    """{"model", "basis", "free", "steps": [{"tokens", "input", "output", "cost", "cost_low", "cost_high"}]}"""
    from app.agents.orchestrator import _lead_model_for
    from app.llm.mock_loop import mock_mode
    from app.llm.providers import PROVIDERS

    if mock_mode():
        return {"model": "Test mode", "free": True, "basis": "Test mode is on: canned answers, no AI cost.",
                "steps": [{"tokens": 0, "input": 0, "output": 0, "cost": 0, "cost_low": 0, "cost_high": 0}
                          for _ in questions]}
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise ValueError("Session not found")
    tools = {t.id: t for t in db.exec(select(Tool)).all()}
    models = [t for t in tools.values() if t.kind == "model"]
    prices = {}
    for t in models:
        p = _prices(t.config)
        from app.llm.tiers import resolve
        mid = resolve(t.config).get("model_id")
        if p and mid:
            prices.setdefault(mid, p)
    lead = db.exec(select(Role).where(Role.is_lead == True)).first()  # noqa: E712
    model = _lead_model_for(chat, lead, tools) if lead else None
    model_id = model.model_id if model else ""
    name = next((t.name for t in models if t.config.get("model_id") == model_id), model_id)
    provider = PROVIDERS.get(model.provider) if model else None
    free = bool(provider and provider.local)
    price = prices.get(model_id)

    hist = _history(db, prices)
    same = [h for h in hist if h["model"] == model_id][:HISTORY]
    pool = same or hist[:HISTORY]
    if pool:
        base_in = statistics.median(h["input"] for h in pool)
        base_out = statistics.median(h["output"] for h in pool)
        calls = statistics.median(h["calls"] for h in pool)
        costed = [h["cost"] for h in same if h["cost"] is not None]
        base_cost = statistics.median(costed) if costed else None
        basis = (f"Based on your last {len(pool)} questions answered by {name}" if same
                 else f"Based on your last {len(pool)} questions (other models), priced for {name}")
    else:
        base_in, base_out, calls, base_cost = FALLBACK["input"], FALLBACK["output"], FALLBACK["calls"], None
        basis = "A rough starting guess: there are no finished questions to learn from yet"
    convo = _conversation_tokens(db, session_id)
    extra_in = convo * calls
    basis += f", plus this chat's length (about {convo // 1000 or 1}k tokens, re-read on each of ~{round(calls)} Lead calls)."

    steps = []
    for q in questions:
        f = scope_factor(q)
        tin, tout = int(base_in * f + extra_in), int(base_out * f)
        if free:
            cost = 0.0
        elif base_cost is not None and price:
            cost = base_cost * f + extra_in * price[2] / 1e6  # the conversation is mostly cached
        elif price:
            cost = (tin * price[0] + tout * price[1]) / 1e6  # no cache history: list price (on the high side)
        else:
            cost = None
        steps.append({"tokens": tin + tout, "input": tin, "output": tout, "scope": round(f, 2),
                      "cost": None if cost is None else round(cost, 4),
                      "cost_low": None if cost is None else round(cost / 2, 4),
                      "cost_high": None if cost is None else round(cost * 2, 4)})
    if free:
        basis += " Local model: no cost."
    elif price is None:
        basis += f" No price is set for {name} (Tools → Models), so only tokens are shown."
    return {"model": name, "free": free, "basis": basis, "steps": steps}
