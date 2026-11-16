"""Before a question runs: which data sources assigned to roles are unavailable, and how much it matters.

A source the user switched off (source or connection disabled) is a deliberate choice and is
not reported. Impact is "high" when the role has no other working source for one of the
source's categories (e.g. no real-time quotes at all), "low" when another source covers it.
"""
import hashlib
from typing import Any

from sqlmodel import Session, select

from app.catalog import CAPABILITIES
from app.models import Connection, Role, Tool
from app.tools.registry import availability


def unavailable_report(db: Session) -> dict[str, Any] | None:
    roles = db.exec(select(Role).where(Role.enabled == True)).all()  # noqa: E712
    tools = {t.id: t for t in db.exec(select(Tool)).all()}
    conns = {c.id: c for c in db.exec(select(Connection)).all()}
    status: dict[int, tuple[bool, str | None]] = {}  # each source checked once (some checks touch the network)

    def check(t: Tool) -> tuple[bool, str | None] | None:
        """None = deliberately off (not reported)."""
        conn = conns.get(t.connection_id) if t.connection_id else None
        if not t.enabled or (conn is not None and not conn.enabled):
            return None
        if t.id not in status:
            status[t.id] = availability(t, conn)
        return status[t.id]

    items = []
    for role in sorted(roles, key=lambda r: (not r.is_lead, r.name)):
        assigned = [tools[i] for i in role.tool_ids if i in tools and tools[i].kind != "model"]
        working = [t for t in assigned if (check(t) or (False,))[0]]
        for t in assigned:
            st = check(t)
            if st is None or st[0]:
                continue
            cats = t.capabilities or ["other"]
            covered = {c: [w.name for w in working if c in (w.capabilities or ["other"])] for c in cats}
            gaps = [c for c, alts in covered.items() if not alts]
            items.append({
                "role": role.name, "role_slug": role.slug, "tool": t.name, "tool_id": t.id, "reason": st[1],
                "categories": [CAPABILITIES.get(c, {"label": c})["label"] for c in cats],
                "impact": "high" if gaps else "low",
                "gaps": [CAPABILITIES.get(c, {"label": c})["label"] for c in gaps],
                "covered_by": sorted({name for alts in covered.values() for name in alts}),
            })
    if not items:
        return None
    key = ",".join(sorted(f"{i['role_slug']}:{i['tool_id']}" for i in items))
    high = sum(i["impact"] == "high" for i in items)
    return {
        "fingerprint": hashlib.sha1(key.encode()).hexdigest()[:16],
        "items": sorted(items, key=lambda i: (i["impact"] != "high", i["role"], i["tool"])),
        "summary": (f"{len(items)} data source{'s' if len(items) != 1 else ''} unavailable"
                    + (f"; {high} leave{'s' if high == 1 else ''} a role with no other source for that data" if high else "")),
    }
