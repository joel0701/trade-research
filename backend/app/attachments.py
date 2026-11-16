"""Storing uploaded documents and turning them into Claude content blocks."""
import base64
import re
import uuid
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.config import MAX_UPLOAD_MB, UPLOAD_DIR
from app.models import Attachment

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".yaml", ".yml"}
SUPPORTED = "PDF, images (PNG, JPG, GIF, WebP) and text files (TXT, MD, CSV, JSON, HTML, XML)"


class UnsupportedFile(ValueError):
    pass


def classify(filename: str) -> tuple[str, str]:
    """Return (kind, media_type) for a filename, or raise UnsupportedFile."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf", "application/pdf"
    if ext in IMAGE_TYPES:
        return "image", IMAGE_TYPES[ext]
    if ext in TEXT_EXTS:
        return "text", "text/plain"
    hint = " Save Word or Excel files as PDF or CSV first." if ext in {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"} else ""
    raise UnsupportedFile(f"{filename}: unsupported file type. Supported: {SUPPORTED}.{hint}")


def save_upload(db: Session, session_id: int, filename: str, data: bytes) -> Attachment:
    kind, media_type = classify(filename)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise UnsupportedFile(f"{filename} is larger than {MAX_UPLOAD_MB} MB.")
    if not data:
        raise UnsupportedFile(f"{filename} is empty.")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:120]
    folder = UPLOAD_DIR / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex[:12]}_{safe}"
    path.write_bytes(data)
    att = Attachment(session_id=session_id, filename=Path(filename).name, media_type=media_type,
                     kind=kind, size=len(data), path=str(path))
    db.add(att)
    db.commit()
    db.refresh(att)
    return att


def content_block(att: Attachment) -> dict[str, Any]:
    """The API content block for an attachment (read from disk at request time)."""
    raw = Path(att.path).read_bytes()
    if att.kind == "image":
        return {"type": "image", "source": {"type": "base64", "media_type": att.media_type,
                                            "data": base64.standard_b64encode(raw).decode()}}
    if att.kind == "pdf":
        return {"type": "document", "title": att.filename,
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.standard_b64encode(raw).decode()}}
    return {"type": "document", "title": att.filename,
            "source": {"type": "text", "media_type": "text/plain", "data": raw.decode("utf-8", errors="replace")}}


def expand_refs(db: Session, content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace stored {"type": "attachment_ref"} blocks with real content blocks."""
    ids = [b["attachment_id"] for b in content if b.get("type") == "attachment_ref"]
    if not ids:
        return content
    found = {a.id: a for a in db.exec(select(Attachment).where(Attachment.id.in_(ids))).all()}
    out = []
    for b in content:
        if b.get("type") != "attachment_ref":
            out.append(b)
            continue
        att = found.get(b["attachment_id"])
        if att is None or not Path(att.path).exists():
            out.append({"type": "text", "text": f"[Attached file {b.get('filename', '')} is no longer available.]"})
        else:
            out.append(content_block(att))
    return out


def delete_files(atts: list[Attachment]) -> None:
    for a in atts:
        Path(a.path).unlink(missing_ok=True)
