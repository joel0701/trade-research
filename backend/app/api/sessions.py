"""Chat sessions: list, read, send a message (streamed as Server-Sent Events)."""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app.agents.orchestrator import ACTIVE_SESSIONS, run_turn
from app.db import get_session
from app.attachments import UnsupportedFile, delete_files, save_upload
from app.models import Attachment, ChatSession, Message, Run, Tool

router = APIRouter(prefix="/sessions", tags=["sessions"])


class NewSession(BaseModel):
    title: str = "New research"
    origin: str = "chat"
    lead_model_tool_id: int | None = None


class SessionUpdate(BaseModel):
    """Partial update: only fields present in the request change.
    Send lead_model_tool_id: null to go back to the Lead role's default model."""
    title: str | None = None
    lead_model_tool_id: int | None = None


class SendMessage(BaseModel):
    text: str = ""
    attachment_ids: list[int] = []


def _check_model(db: Session, tool_id: int | None) -> None:
    if tool_id is None:
        return
    t = db.get(Tool, tool_id)
    if t is None or t.kind != "model":
        raise HTTPException(422, "lead_model_tool_id must refer to a model in Tools")


def _attachment_info(a: Attachment) -> dict[str, Any]:
    return {"id": a.id, "filename": a.filename, "kind": a.kind, "size": a.size}


def _visible_messages(db: Session, session_id: int) -> list[dict[str, Any]]:
    """Messages as the UI shows them: user text and assistant text, no tool plumbing."""
    rows = db.exec(select(Message).where(Message.session_id == session_id, Message.visible == True)  # noqa: E712
                   .order_by(Message.id)).all()
    out = []
    for m in rows:
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
                        "attachments": atts,
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


@router.get("/{session_id}/runs")
def runs(session_id: int, db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Trace of every Lead and specialist run in this session."""
    rows = db.exec(select(Run).where(Run.session_id == session_id).order_by(Run.id)).all()
    return [r.model_dump(mode="json") for r in rows]


@router.post("/{session_id}/messages")
async def send(session_id: int, body: SendMessage, db: Session = Depends(get_session)) -> StreamingResponse:
    if db.get(ChatSession, session_id) is None:
        raise HTTPException(404)
    if session_id in ACTIVE_SESSIONS:
        raise HTTPException(409, "This session is still working on the previous question.")
    if not body.text.strip() and not body.attachment_ids:
        raise HTTPException(422, "Type a question or attach a file.")
    text = body.text.strip() or "Please analyse the attached document(s)."

    async def events():
        try:
            async for event in run_turn(session_id, text, body.attachment_ids):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
