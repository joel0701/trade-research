"""Runs one Lead Analyst turn in a chat session.

The Lead gets one `consult_<role>` tool per enabled specialist. Calling it runs that
specialist's own agent loop (its model, prompt and tools) and returns its findings.
Specialists consulted in the same turn run in parallel.
"""
import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.agents.loop import Emit, ModelConfig, run_agent
from app.db import engine
from app.attachments import expand_refs
from app.llm.mock_loop import mock_mode
from app.llm.providers import model_available
from app.models import Attachment, ChatSession, Message, Role, Run, Tool, now
from app.tools.registry import Toolbox, safe_name

log = logging.getLogger(__name__)
_running: set[asyncio.Task] = set()
# Sessions with a turn in progress. In-memory, so it assumes one backend process
# (true locally; revisit before running several instances on AWS).
ACTIVE_SESSIONS: set[int] = set()


class TurnInProgress(Exception):
    pass


def mark_interrupted_runs() -> int:
    """On startup, any run still 'running' was cut off by a restart; record that."""
    with Session(engine) as db:
        stale = db.exec(select(Run).where(Run.status == "running")).all()
        for r in stale:
            r.status, r.error, r.finished_at = "error", "Interrupted: the backend restarted during this run.", now()
            db.add(r)
        db.commit()
        return len(stale)

SPECIALIST_SUFFIX = """

You are a specialist on a research team and report to the Lead Analyst, not the end user.
Use your tools to gather evidence, then reply with concise, factual findings: key numbers,
dates and sources. Flag uncertainty and stale data explicitly. Do not pad the answer."""

LEAD_SUFFIX = """

Today's date is {today}.
Your team (consult them with the consult_* tools; call several at once when their work is independent):
{team}
Consult only the specialists the question needs. Simple questions may need none.
Documents the user attaches are visible only to you. When a specialist needs their contents,
put the relevant figures or excerpts in the consult context.
This is research and education, not personalised financial advice."""


MOCK_MODEL = ModelConfig(model_id="mock", provider="mock", effort=None, max_tokens=1000)


def _model_for(role: Role, tools: dict[int, Tool]) -> ModelConfig:
    if mock_mode():  # MOCK_LLM=1: every role uses the free mock
        return MOCK_MODEL
    t = tools.get(role.model_tool_id) if role.model_tool_id else None
    return ModelConfig.from_tool_config(t.config) if t else ModelConfig()


def _start_run(session_id: int, role: Role, text: str, parent: int | None, model: str | None = None) -> int:
    with Session(engine) as db:
        run = Run(session_id=session_id, role_slug=role.slug, input=text, parent_run_id=parent, model=model)
        db.add(run)
        db.commit()
        return run.id


def _finish_run(run_id: int, result: Any, error: str | None = None) -> None:
    with Session(engine) as db:
        run = db.get(Run, run_id)
        run.output = result.text if result else ""
        run.tool_calls = result.tool_calls if result else []
        run.usage = result.usage if result else {}
        run.error = error or (result.error if result else None)
        run.status = "error" if run.error else "ok"
        run.finished_at = now()
        db.add(run)
        db.commit()


def repair_history(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop an assistant tool_use turn left without results (e.g. after a crash),
    which the API would otherwise reject."""
    out: list[dict[str, Any]] = []
    for i, m in enumerate(msgs):
        has_tool_use = m["role"] == "assistant" and any(b.get("type") == "tool_use" for b in m["content"])
        nxt = msgs[i + 1] if i + 1 < len(msgs) else None
        answered = nxt is not None and nxt["role"] == "user" and any(
            b.get("type") == "tool_result" for b in nxt["content"])
        if has_tool_use and not answered:
            continue
        out.append(m)
    return out


async def consult(specialist: Role, question: str, session_id: int, tools: dict[int, Tool],
                  emit: Emit, parent_run: int) -> str:
    model = _model_for(specialist, tools)
    run_id = _start_run(session_id, specialist, question, parent_run, model.model_id)
    await emit({"type": "specialist_start", "role": specialist.slug, "name": specialist.name, "question": question})
    result = None
    try:
        async with Toolbox([tools[i] for i in specialist.tool_ids if i in tools]) as toolbox:
            result = await run_agent(
                model=model,
                system=specialist.system_prompt + SPECIALIST_SUFFIX,
                messages=[{"role": "user", "content": question}],
                toolbox=toolbox, emit=emit, stream_text=False,
                max_iterations=specialist.max_iterations, source=specialist.slug,
            )
        _finish_run(run_id, result)
        answer = result.text or "(no findings)"
        if result.error:
            answer += f"\n[note: {result.error}]"
    except Exception as e:
        log.exception("Specialist %s failed", specialist.slug)
        _finish_run(run_id, result, error=str(e))
        answer = f"{specialist.name} failed: {e}"
    await emit({"type": "specialist_end", "role": specialist.slug, "name": specialist.name, "summary": answer[:300]})
    return answer


async def run_turn(session_id: int, user_text: str, attachment_ids: list[int] | None = None,
                   excluded: list[str] | None = None, hidden: bool = False) -> AsyncIterator[dict[str, Any]]:
    """Persist the user message, run the Lead, and stream events to the caller.

    The turn runs as a background task: if the caller disconnects (the user leaves the
    page), it still finishes and is saved. ACTIVE_SESSIONS tells the UI it is running.
    """
    if session_id in ACTIVE_SESSIONS:
        raise TurnInProgress(f"Session {session_id} is already working on a question.")
    ACTIVE_SESSIONS.add(session_id)
    try:
        prepared = _prepare_turn(session_id, user_text, attachment_ids, hidden)
    except Exception:
        ACTIVE_SESSIONS.discard(session_id)
        raise
    async for event in _run_prepared(session_id, user_text, *prepared, excluded=excluded or []):
        yield event


def _lead_model_for(chat: ChatSession, lead: Role, tools: dict[int, Tool]) -> ModelConfig:
    """The session's chosen Lead model if set and usable, else the Lead role's default."""
    if mock_mode():
        return MOCK_MODEL
    t = tools.get(chat.lead_model_tool_id) if chat.lead_model_tool_id else None
    if t is not None and t.kind == "model" and t.enabled and model_available(t.config):
        return ModelConfig.from_tool_config(t.config)
    # The chosen model is gone or its vendor key was removed: use the Lead role's model.
    return _model_for(lead, tools)


def apply_scope(scope: list[int] | None, lead: Role, specialists: list[Role],
                tools: dict[int, Tool]) -> tuple[Role, list[Role]]:
    """Limit a session to a set of data sources (e.g. a scanner's). Works on copies, so role
    settings are never changed. Specialists left with no sources aren't offered to the Lead."""
    if not scope:
        return lead, specialists
    allowed = set(scope)

    def scoped(r: Role) -> Role:
        copy = Role.model_validate(r.model_dump())
        copy.tool_ids = [i for i in r.tool_ids if i in allowed]
        return copy

    names = ", ".join(tools[i].name for i in scope if i in tools)
    lead = scoped(lead)
    specialists = [s for s in map(scoped, specialists) if s.tool_ids]
    # A chosen source no specialist has is given to the Lead, so it still gets used.
    held = {i for s in specialists for i in s.tool_ids} | set(lead.tool_ids)
    lead.tool_ids += [i for i in scope if i not in held and i in tools and tools[i].kind != "model"]
    lead.system_prompt += (f"\n\nThis session is limited to these data sources: {names}. Work from them and the "
                           "information already in the conversation.")
    return lead, specialists


def _prepare_turn(session_id: int, user_text: str, attachment_ids: list[int] | None = None, hidden: bool = False):
    # expire_on_commit=False: roles/tools are used after this block closes.
    with Session(engine, expire_on_commit=False) as db:
        chat = db.get(ChatSession, session_id)
        if chat is None:
            raise ValueError(f"No session {session_id}")
        tools = {t.id: t for t in db.exec(select(Tool)).all()}
        roles = db.exec(select(Role).where(Role.enabled == True)).all()  # noqa: E712
        lead = next((r for r in roles if r.is_lead), None)
        if lead is None:
            raise ValueError("No enabled Lead role configured")
        specialists = [r for r in roles if not r.is_lead]
        lead, specialists = apply_scope(chat.tool_scope, lead, specialists, tools)
        history = [{"role": m.role, "content": expand_refs(db, m.content)} for m in
                   db.exec(select(Message).where(Message.session_id == session_id, Message.role != "notice")
                           .order_by(Message.id)).all()]

        atts = []
        if attachment_ids:
            atts = db.exec(select(Attachment).where(Attachment.id.in_(attachment_ids))).all()
            bad = [a.id for a in atts if a.session_id != session_id or a.message_id is not None]
            if bad or len(atts) != len(set(attachment_ids)):
                raise ValueError("Some attachments are missing, already sent, or belong to another session.")
        # Documents go before the question (Claude reads them first). Stored as references
        # so files stay on disk instead of being copied into every saved message.
        user_content = [{"type": "attachment_ref", "attachment_id": a.id, "filename": a.filename} for a in atts]
        user_content.append({"type": "text", "text": user_text})
        msg = Message(session_id=session_id, role="user", content=user_content, visible=not hidden)
        db.add(msg)
        db.flush()
        for a in atts:
            a.message_id = msg.id
            db.add(a)
        if chat.title == "New research":
            first = user_text.strip().splitlines()[0][:80] if user_text.strip() else ""
            chat.title = first or (atts[0].filename if atts else chat.title)
        chat.updated_at = now()
        db.add(chat)
        db.commit()
        current = expand_refs(db, user_content)
        lead_model = _lead_model_for(chat, lead, tools)

    messages = repair_history(history) + [{"role": "user", "content": current}]
    return tools, lead, specialists, messages, lead_model


async def _run_prepared(session_id: int, user_text: str, tools: dict[int, Tool], lead: Role,
                        specialists: list[Role], messages: list[dict[str, Any]],
                        lead_model: ModelConfig, excluded: list[str] | None = None) -> AsyncIterator[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)


    current_run: dict[str, int | None] = {"id": None}

    def on_message(role: str, content: list, visible: bool, usage: dict | None) -> None:
        with Session(engine) as db:
            db.add(Message(session_id=session_id, role=role, content=content, visible=visible,
                           run_id=current_run["id"],
                           model=lead_model.model_id if role == "assistant" else None, usage=usage))
            db.commit()

    async def work() -> None:
        run_id = _start_run(session_id, lead, user_text, None, lead_model.model_id)
        current_run["id"] = run_id
        result = None
        try:
            async with Toolbox([tools[i] for i in lead.tool_ids if i in tools]) as toolbox:
                for spec in specialists:
                    async def run_spec(args: dict, _spec=spec) -> str:
                        q = args.get("question", "")
                        if args.get("context"):
                            q += f"\n\nContext from the Lead:\n{args['context']}"
                        return await consult(_spec, q, session_id, tools, emit, run_id)

                    toolbox.add({
                        "name": safe_name(f"consult_{spec.slug}"),
                        "description": f"Ask the {spec.name}. {spec.description}",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "description": "Specific question or task"},
                                "context": {"type": "string", "description": "Relevant context: tickers, timeframe, findings so far"},
                            },
                            "required": ["question"],
                        },
                    }, run_spec)
                team = "\n".join(f"- consult_{s.slug}: {s.name}. {s.description}" for s in specialists) or "(none)"
                system = lead.system_prompt + LEAD_SUFFIX.format(today=date.today().isoformat(), team=team)
                if excluded:
                    system += ("\n\nThese data sources are unavailable for this question; the user chose to continue "
                               "without them: " + "; ".join(excluded) + ". Where that limits the answer, say so briefly.")
                result = await run_agent(model=lead_model, system=system, messages=messages, toolbox=toolbox,
                                         emit=emit, on_message=on_message, max_iterations=lead.max_iterations)
            _finish_run(run_id, result)
            if result.error:
                await emit({"type": "error", "message": result.error})
            await emit({"type": "done", "usage": result.usage})
        except Exception as e:
            log.exception("Lead turn failed")
            _finish_run(run_id, result, error=str(e))
            await emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            ACTIVE_SESSIONS.discard(session_id)
            await queue.put(None)

    # Keep a reference so the turn finishes (and is saved) even if the client disconnects.
    task = asyncio.create_task(work())
    _running.add(task)
    task.add_done_callback(_running.discard)
    while (event := await queue.get()) is not None:
        yield event
