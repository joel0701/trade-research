"""Default models: low-level, mid-level and high-level (Settings → Default models).

Each is a model entry in Tools (slugs default_low / default_mid / default_high, provider "tier") that
points at a real model chosen in Settings. Skills, team members and sessions can pick a default
instead of a specific model; changing the default in Settings changes all of them at once.
"""
from typing import Any

from sqlmodel import Session, select

TIERS = ("low", "mid", "high")
TIER_SLUGS = {"low": "default_low", "mid": "default_mid", "high": "default_high"}
TIER_LABELS = {"low": "Default: low-level", "mid": "Default: mid-level", "high": "Default: high-level"}
FALLBACK = {"low": "haiku", "mid": "sonnet", "high": "opus"}  # used until set in Settings

_targets: dict[str, int | None] = {t: None for t in TIERS}  # tier -> Tool id (from the profile)


def is_tier(cfg: dict[str, Any] | None) -> bool:
    return bool(cfg) and cfg.get("provider") == "tier"


def set_targets(low: int | None, mid: int | None, high: int | None) -> None:
    _targets.update(low=low, mid=mid, high=high)


def target_tool(tier: str):
    """The real model a default points at (the Settings choice, else Haiku/Sonnet/Opus), or None."""
    from app.db import engine
    from app.models import Tool

    with Session(engine) as db:
        t = db.get(Tool, _targets.get(tier)) if _targets.get(tier) else None
        if t is None or t.kind != "model" or is_tier(t.config):
            t = db.exec(select(Tool).where(Tool.slug == FALLBACK.get(tier, "sonnet"))).first()
        if t is not None:
            db.expunge(t)
        return t


def resolve(cfg: dict[str, Any]) -> dict[str, Any]:
    """A model config with any default replaced by the real model's config."""
    if not is_tier(cfg):
        return cfg
    t = target_tool(cfg.get("tier", "mid"))
    return dict(t.config) if t is not None else {"provider": "anthropic", "model_id": "claude-sonnet-5-5"}
