"""Chat sessions: list, read, send a message (streamed as Server-Sent Events)."""
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app.agents.orchestrator import ACTIVE_SESSIONS, run_turn
from app.db import get_session
from app.attachments import UnsupportedFile, copy_attachment, delete_files, save_upload
from app.llm.providers import model_available, provider_of
from app.preflight import unavailable_report
from app.skill_draft import DraftError, draft_skill
from app.models import Attachment, ChatSession, Message, Run, Tool, now

router = APIRouter(prefix="/sessions", tags=["sessions"])


class NewSession(BaseModel):
    title: str = "New research"
    origin: str = "chat"
    lead_model_tool_id: int | None = None


class SessionUpdate(BaseModel):
    """Partial update: only fields present in the request change.
    Send lead_model_tool_id: null to go back to the Lead role's default model.
    Send tool_scope: null to lift a scanner session's data-source limit (use the full team)."""
    title: str | None = None
    lead_model_tool_id: int | None = None
    tool_scope: list[int] | None = None


class Fork(BaseModel):
    message_id: int  # copy the conversation up to and including this message


class Feedback(BaseModel):
    rating: Literal["up", "down"] | None  # None clears it


class SendMessage(BaseModel):
    text: str = ""
    attachment_ids: list[int] = []
    # Set after the user saw the unavailable-sources prompt: the fingerprint they agreed to,
    # and whether to stop asking about that same set for the rest of the session.
    proceed: str | None = None
    remember: bool = False
    hidden: bool = False  # sent by the app (e.g. to start a scanner's analysis); not shown in the chat


def _check_model(db: Session, tool_id: int | None) -> None:
    check_model(db, tool_id)


def check_model(db: Session, tool_id: int | None) -> None:
    """A selectable Lead model must be a model whose vendor API key is configured."""
    if tool_id is None:
        return
    t = db.get(Tool, tool_id)
    if t is None or t.kind != "model":
        raise HTTPException(422, "lead_model_tool_id must refer to a model in Tools")
    if not t.enabled:
        raise HTTPException(422, f"{t.name} is disabled in Tools")
    if not model_available(t.config):
        p = provider_of(t.config)
        raise HTTPException(422, f"{t.name} needs an API key: set {p.key_env} in .env and restart the backend")


def _attachment_info(a: Attachment) -> dict[str, Any]:
    return {"id": a.id, "filename": a.filename, "kind": a.kind, "size": a.size}


def record_notice(db: Session, session_id: int, decision: str, report: dict[str, Any], question: str) -> None:
    """Add a record to the conversation of what was unavailable and what the user decided.
    Shown in the chat; never sent to the model."""
    items = [{k: i.get(k) for k in ("role", "tool", "reason", "impact", "gaps", "covered_by")} for i in report["items"]]
    db.add(Message(session_id=session_id, role="notice", visible=True, content=[{
        "type": "notice", "kind": "unavailable_sources", "decision": decision, "summary": report["summary"],
        "items": items, "question": question[:300]}]))


def _visible_messages(db: Session, session_id: int) -> list[dict[str, Any]]:
    """Messages as the UI shows them: user text and assistant text, no tool plumbing."""
    rows = db.exec(select(Message).where(Message.session_id == session_id, Message.visible == True)  # noqa: E712
                   .order_by(Message.id)).all()
    out = []
    for m in rows:
        if m.role == "notice":
            out.append({"id": m.id, "role": "notice", "text": "", "notice": m.content[0] if m.content else {},
                        "created_at": m.created_at.isoformat()})
            continue
        # An assistant message that calls tools is an interim note ("let me check..."),
        # not the answer. It stays in the history and the trace, but not in the chat.
        if m.role == "assistant" and any(b.get("type") == "tool_use" for b in m.content):
            continue
        text = "".join(b.get("text", "") for b in m.content if b.get("type") == "text")
        refs = [b["attachment_id"] for b in m.content if b.get("type") == "attachment_ref"]
        thinking = "".join(b.get("thinking", "") for b in m.content if b.get("type") == "thinking")
        if text.strip() or m.role == "user":
            atts = [_attachment_info(a) for a in db.exec(select(Attachment).where(Attachment.id.in_(refs))).all()] if refs else []
            out.append({"id": m.id, "role": m.role, "text": text, "thinking": thinking, "run_id": m.run_id,
                        "attachments": atts, "model": m.model, "feedback": m.feedback,
                        "created_at": m.created_at.isoformat()})
    return out


@router.get("")
def list_sessions(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = db.exec(select(ChatSession).order_by(ChatSession.updated_at.desc())).all()
    return [{**r.model_dump(mode="json"), "running": r.id in ACTIVE_SESSIONS} for r in rows]


@router.post("")
def create_session(body: NewSession, db: Session = Depends(get_session)) -> dict[str, Any]:
    _check_model(db, body.lead_model_tool_id)
    chat = ChatSession(title=body.title, origin=body.origin, lead_model_tool_id=body.lead_model_tool_id)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat.model_dump(mode="json")


@router.get("/{session_id}")
def get_chat(session_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise HTTPException(404)
    return {**chat.model_dump(mode="json"), "running": session_id in ACTIVE_SESSIONS,
            "messages": _visible_messages(db, session_id)}


@router.patch("/{session_id}")
def update_session(session_id: int, body: SessionUpdate, db: Session = Depends(get_session)) -> dict[str, Any]:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise HTTPException(404)
    if "title" in body.model_fields_set:
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(422, "Title cannot be empty")
        chat.title = title[:200]
    if "lead_model_tool_id" in body.model_fields_set:
        _check_model(db, body.lead_model_tool_id)
        chat.lead_model_tool_id = body.lead_model_tool_id
    if "tool_scope" in body.model_fields_set:
        chat.tool_scope = body.tool_scope or None
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat.model_dump(mode="json")


@router.post("/{session_id}/attachments")
async def upload(session_id: int, file: UploadFile, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Upload a document; send its id with the next message to include it."""
    if db.get(ChatSession, session_id) is None:
        raise HTTPException(404)
    try:
        att = save_upload(db, session_id, file.filename or "upload", await file.read())
    except UnsupportedFile as e:
        raise HTTPException(415, str(e)) from e
    return _attachment_info(att)


@router.delete("/{session_id}/attachments/{attachment_id}")
def remove_attachment(session_id: int, attachment_id: int, db: Session = Depends(get_session)) -> dict[str, bool]:
    att = db.get(Attachment, attachment_id)
    if att is None or att.session_id != session_id:
        raise HTTPException(404)
    if att.message_id is not None:
        raise HTTPException(409, "This file was already sent and is part of the conversation.")
    delete_files([att])
    db.delete(att)
    db.commit()
    return {"ok": True}


@router.get("/{session_id}/attachments/{attachment_id}")
def download_attachment(session_id: int, attachment_id: int, db: Session = Depends(get_session)) -> FileResponse:
    att = db.get(Attachment, attachment_id)
    if att is None or att.session_id != session_id:
        raise HTTPException(404)
    return FileResponse(att.path, filename=att.filename)


@router.delete("/{session_id}")
def delete_chat(session_id: int, db: Session = Depends(get_session)) -> dict[str, bool]:
    if session_id in ACTIVE_SESSIONS:
        raise HTTPException(409, "Wait for this session to finish before deleting it.")
    atts = db.exec(select(Attachment).where(Attachment.session_id == session_id)).all()
    delete_files(atts)
    db.exec(delete(Attachment).where(Attachment.session_id == session_id))
    db.exec(delete(Message).where(Message.session_id == session_id))
    db.exec(delete(Run).where(Run.session_id == session_id))
    chat = db.get(ChatSession, session_id)
    if chat:
        db.delete(chat)
    db.commit()
    return {"ok": True}


@router.put("/{session_id}/messages/{message_id}/feedback")
def set_feedback(session_id: int, message_id: int, body: Feedback, db: Session = Depends(get_session)) -> dict[str, Any]:
    msg = db.get(Message, message_id)
    if msg is None or msg.session_id != session_id or msg.role != "assistant":
        raise HTTPException(404)
    msg.feedback = body.rating
    db.add(msg)
    db.commit()
    return {"id": msg.id, "feedback": msg.feedback}


@router.post("/{session_id}/fork")
def fork(session_id: int, body: Fork, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Start a new session holding this conversation up to a message, to explore another direction.
    Messages, attachments (files copied) and trace runs are copied, so the two sessions are independent."""
    src = db.get(ChatSession, session_id)
    cut = db.get(Message, body.message_id)
    if src is None or cut is None or cut.session_id != session_id:
        raise HTTPException(404)
    new = ChatSession(title=f"{src.title} (fork)"[:200], origin=f"fork:{session_id}",
                      lead_model_tool_id=src.lead_model_tool_id)
    db.add(new)
    db.flush()

    msgs = db.exec(select(Message).where(Message.session_id == session_id, Message.id <= cut.id)
                   .order_by(Message.id)).all()

    # Copy the trace runs those messages point to (and the specialist runs under them).
    lead_ids = {m.run_id for m in msgs if m.run_id}
    runs = db.exec(select(Run).where(Run.session_id == session_id)).all()
    keep = [r for r in runs if r.id in lead_ids or r.parent_run_id in lead_ids]
    run_map: dict[int, int] = {}
    for r in sorted(keep, key=lambda r: (r.parent_run_id is not None, r.id)):  # parents first
        data = r.model_dump(exclude={"id"})
        data.update(session_id=new.id, parent_run_id=run_map.get(r.parent_run_id) if r.parent_run_id else None)
        copy = Run(**data)
        db.add(copy)
        db.flush()
        run_map[r.id] = copy.id

    att_map: dict[int, int] = {}
    for m in msgs:
        content = []
        for b in m.content:
            if b.get("type") == "attachment_ref":
                old = db.get(Attachment, b["attachment_id"])
                if old is not None and b["attachment_id"] not in att_map:
                    att = copy_attachment(db, old, new.id)
                    att_map[old.id] = att.id
                b = {**b, "attachment_id": att_map.get(b["attachment_id"], b["attachment_id"])}
            content.append(b)
        copy = Message(session_id=new.id, role=m.role, content=content, visible=m.visible, model=m.model,
                       usage=m.usage, feedback=None, run_id=run_map.get(m.run_id) if m.run_id else None)
        db.add(copy)
        db.flush()
        for old_id, new_id in att_map.items():
            att = db.get(Attachment, new_id)
            if att.message_id is None and any(b.get("attachment_id") == new_id for b in content
                                              if b.get("type") == "attachment_ref"):
                att.message_id = copy.id
                db.add(att)
    db.commit()
    db.refresh(new)
    return new.model_dump(mode="json")


@router.post("/{session_id}/skill-draft")
async def skill_draft(session_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Draft a reusable skill from this conversation, for the user to review and save."""
    if db.get(ChatSession, session_id) is None:
        raise HTTPException(404)
    convo = [m for m in _visible_messages(db, session_id) if m["text"].strip()]
    if not any(m["role"] == "user" for m in convo):
        raise HTTPException(422, "Ask at least one question before saving the conversation as a skill.")
    try:
        draft = await draft_skill(db, convo)
    except DraftError as e:
        raise HTTPException(422, str(e)) from e
    # Pre-select the data sources this conversation actually used (their notes come with them).
    used = {tc["name"] for r in db.exec(select(Run).where(Run.session_id == session_id)).all()
            for tc in (r.tool_calls or [])}
    tools = db.exec(select(Tool).where(Tool.kind != "model")).all()
    draft["tool_ids"] = [t.id for t in tools if t.slug in used]
    return draft


@router.post("/{session_id}/pending/cancel")
def cancel_pending(session_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    """The user cancelled a question held back by the unavailable-sources prompt: record it."""
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise HTTPException(404)
    pending = chat.pending_confirm
    if pending:
        record_notice(db, session_id, "cancelled", pending["report"],
                      "(scanner analysis)" if pending.get("hidden") else pending["text"])
        chat.pending_confirm = None
        db.add(chat)
        db.commit()
    return {"text": "" if (pending or {}).get("hidden") else (pending or {}).get("text", "")}


@router.get("/{session_id}/runs")
def runs(session_id: int, db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Trace of every Lead and specialist run in this session."""
    rows = db.exec(select(Run).where(Run.session_id == session_id).order_by(Run.id)).all()
    return [r.model_dump(mode="json") for r in rows]


@router.post("/{session_id}/messages")
async def send(session_id: int, body: SendMessage, db: Session = Depends(get_session)) -> StreamingResponse:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise HTTPException(404)
    if session_id in ACTIVE_SESSIONS:
        raise HTTPException(409, "This session is still working on the previous question.")
    if not body.text.strip() and not body.attachment_ids:
        raise HTTPException(422, "Type a question or attach a file.")
    text = body.text.strip() or "Please analyse the attached document(s)."

    # Some assigned data sources are down: ask the user before running. The held question is kept
    # on the session (so the prompt survives switching screens) and the decision is recorded.
    report = unavailable_report(db, chat.next_turn_tool_scope or chat.tool_scope)
    excluded: list[str] = []
    if report:
        fp = report["fingerprint"]
        explicit, remembered = body.proceed == fp, chat.ack_unavailable == fp
        if not (explicit or remembered):
            atts = db.exec(select(Attachment).where(Attachment.id.in_(body.attachment_ids))).all() if body.attachment_ids else []
            chat.pending_confirm = {"text": body.text, "attachments": [_attachment_info(a) for a in atts],
                                    "report": report, "hidden": body.hidden, "at": now().isoformat()}
            db.add(chat)
            db.commit()
            event = {"type": "confirm_needed", **report}
            return StreamingResponse(iter([f"data: {json.dumps(event)}\n\n"]), media_type="text/event-stream")
        decision = "continued_remember" if (explicit and body.remember) else "continued" if explicit else "remembered"
        if body.remember:
            chat.ack_unavailable = fp
        record_notice(db, session_id, decision, report, "(scanner analysis)" if body.hidden else text)
        chat.pending_confirm = None
        db.add(chat)
        db.commit()
        excluded = [f"{i['tool']} ({i['role']}: {i['reason']})" for i in report["items"]]

    async def events():
        try:
            async for event in run_turn(session_id, text, body.attachment_ids, excluded, hidden=body.hidden):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
