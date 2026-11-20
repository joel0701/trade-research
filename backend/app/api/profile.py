"""User profile and display preferences (single local user)."""
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.config import UPLOAD_DIR
from app.db import get_session
from app.models import UserProfile, now

router = APIRouter(prefix="/profile", tags=["profile"])

AVATAR_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
MAX_AVATAR_BYTES = 2 * 1024 * 1024
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    timezone: str | None = None
    hour12: bool | None = None
    mock_llm: bool | None = None
    model_low_id: int | None = None
    model_mid_id: int | None = None
    model_high_id: int | None = None


def get_profile(db: Session) -> UserProfile:
    p = db.get(UserProfile, 1)
    if p is None:
        p = UserProfile(id=1)
        db.add(p)
        db.commit()
        db.refresh(p)
    return p


def _out(p: UserProfile) -> dict[str, Any]:
    from app.llm.mock_loop import mock_env

    data = p.model_dump(mode="json", exclude={"avatar_path"})
    data["mock_llm_env"] = mock_env()  # MOCK_LLM=1 in .env forces it on (the toggle can't turn it off)
    # Cache-busting query so a new photo shows immediately.
    data["avatar_url"] = f"/api/profile/avatar?v={int(p.updated_at.timestamp())}" if p.avatar_path else None
    return data


@router.get("")
def read(db: Session = Depends(get_session)) -> dict[str, Any]:
    return _out(get_profile(db))


@router.put("")
def update(body: ProfileUpdate, db: Session = Depends(get_session)) -> dict[str, Any]:
    p = get_profile(db)
    if body.name is not None:
        p.name = body.name.strip()[:100]
    if body.email is not None:
        email = body.email.strip()
        if email and not EMAIL.match(email):
            raise HTTPException(422, "That doesn't look like an email address.")
        p.email = email[:200]
    if body.timezone is not None:
        tz = body.timezone.strip() or "auto"
        if tz != "auto":
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise HTTPException(422, f"Unknown time zone: {tz}") from e
        p.timezone = tz
    if body.hour12 is not None:
        p.hour12 = body.hour12
    if body.mock_llm is not None:
        from app.llm.mock_loop import set_mock

        p.mock_llm = body.mock_llm
        set_mock(p.mock_llm)  # takes effect immediately
    for field in ("model_low_id", "model_mid_id", "model_high_id"):
        if field in body.model_fields_set:
            tid = getattr(body, field)
            if tid is not None:
                from app.llm.tiers import is_tier
                from app.models import Tool
                t = db.get(Tool, tid)
                if t is None or t.kind != "model" or is_tier(t.config):
                    raise HTTPException(422, "A default must point at a real model (not another default).")
            setattr(p, field, tid)
    from app.llm.tiers import set_targets

    set_targets(p.model_low_id, p.model_mid_id, p.model_high_id)
    p.updated_at = now()
    db.add(p)
    db.commit()
    db.refresh(p)
    return _out(p)


@router.post("/avatar")
async def upload_avatar(file: UploadFile, db: Session = Depends(get_session)) -> dict[str, Any]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AVATAR_TYPES:
        raise HTTPException(415, "Use a PNG, JPG, WebP or GIF image.")
    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "Images must be 2 MB or smaller.")
    p = get_profile(db)
    folder = UPLOAD_DIR / "profile"
    folder.mkdir(parents=True, exist_ok=True)
    if p.avatar_path:
        Path(p.avatar_path).unlink(missing_ok=True)
    path = folder / f"avatar{ext}"
    path.write_bytes(data)
    p.avatar_path, p.updated_at = str(path), now()
    db.add(p)
    db.commit()
    db.refresh(p)
    return _out(p)


@router.delete("/avatar")
def delete_avatar(db: Session = Depends(get_session)) -> dict[str, Any]:
    p = get_profile(db)
    if p.avatar_path:
        Path(p.avatar_path).unlink(missing_ok=True)
    p.avatar_path, p.updated_at = None, now()
    db.add(p)
    db.commit()
    db.refresh(p)
    return _out(p)


@router.get("/avatar")
def avatar(db: Session = Depends(get_session)) -> FileResponse:
    p = get_profile(db)
    if not p.avatar_path or not Path(p.avatar_path).exists():
        raise HTTPException(404)
    return FileResponse(p.avatar_path, media_type=AVATAR_TYPES.get(Path(p.avatar_path).suffix.lower()))
