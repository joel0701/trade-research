"""Chat sessions: list, read, send a message (streamed as Server-Sent Events)."""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app.agents.orchestrator import run_turn
from app.db import get_session
from app.models import ChatSession, Message, Run

router = APIRouter(prefix="/sessions", tags=["sessions"])


class NewSession(BaseModel):
    title: str = "New research"
    origin: str = "chat"


class SendMessage(BaseModel):
    text: str


def _visible_messages(db: Session, session_id: int) -> list[dict[str, Any]]:
    """Messages as the UI shows them: user text and assistant text, no tool plumbing."""
    rows = db.exec(select(Message).where(Message.session_id == session_id, Message.visible == True)  # noqa: E712
                   .order_by(Message.id)).all()
    out = []
    for m in rows:
        text = "".join(b.get("text", "") for b in m.content if b.get("type") == "text")
        thinking = "".join(b.get("thinking", "") for b in m.content if b.get("type") == "thinking")
        if text.strip() or m.role == "user":
            out.append({"id": m.id, "role": m.role, "text": text, "thinking": thinking,
                        "created_at": m.created_at.isoformat()})
    return out


@router.get("")
def list_sessions(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = db.exec(select(ChatSession).order_by(ChatSession.updated_at.desc())).all()
    return [r.model_dump(mode="json") for r in rows]


@router.post("")
def create_session(body: NewSession, db: Session = Depends(get_session)) -> dict[str, Any]:
    chat = ChatSession(title=body.title, origin=body.origin)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat.model_dump(mode="json")


@router.get("/{session_id}")
def get_chat(session_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise HTTPException(404)
    return {**chat.model_dump(mode="json"), "messages": _visible_messages(db, session_id)}


@router.patch("/{session_id}")
def rename(session_id: int, body: NewSession, db: Session = Depends(get_session)) -> dict[str, Any]:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise HTTPException(404)
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "Title cannot be empty")
    chat.title = title[:200]
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat.model_dump(mode="json")


@router.delete("/{session_id}")
def delete_chat(session_id: int, db: Session = Depends(get_session)) -> dict[str, bool]:
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

    async def events():
        try:
            async for event in run_turn(session_id, body.text):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
