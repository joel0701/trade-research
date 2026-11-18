"""Data source notes: seeded, editable, shown to agents, and added to skill/scanner prompts."""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.models import Tool
from app.tools.registry import Toolbox


def tools_by_slug(c):
    return {t["slug"]: t for t in c.get("/api/tools").json()}


def test_notes_seeded_and_user_notes_kept():
    with TestClient(app) as c:
        t = tools_by_slug(c)
        assert "up to 60 trading days" in t["finra_short_volume"]["notes"]
        assert "2 weeks (Tier 1) to 4 weeks" in t["finra_ats_weekly"]["notes"]
        c.put(f"/api/tools/{t['fundamentals']['id']}", json={"notes": "My own note"})
    with TestClient(app) as c:  # restart: seeding doesn't overwrite your notes
        assert tools_by_slug(c)["fundamentals"]["notes"] == "My own note"
        c.put(f"/api/tools/{tools_by_slug(c)['fundamentals']['id']}", json={"notes": ""})  # tidy up


async def test_agents_see_notes_in_tool_description():
    tool = Tool(id=1, slug="finra_short_volume", name="FINRA", kind="python",
                config={"function": "finra_short_volume"}, notes="Lookback is fixed: up to 60 trading days.")
    async with Toolbox([tool], {}) as tb:
        assert tb.definitions[0]["description"].endswith("Notes: Lookback is fixed: up to 60 trading days.")


def test_skill_prompt_has_data_notes_and_scope():
    with TestClient(app) as c:
        t = tools_by_slug(c)
        sources = [t["finra_short_volume"]["id"], t["finra_ats_weekly"]["id"], t["ddg_search"]["id"]]
        skill = c.post("/api/skills", json={"slug": "dark_pool_check", "name": "Dark pool check",
                                            "prompt_template": "Assess dark pool positioning for {ticker}.",
                                            "params": [{"name": "ticker", "label": "Ticker"}], "tool_ids": sources}).json()
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "CRWV"}}).json()
        assert r["prompt"].startswith("Assess dark pool positioning for **CRWV**.\n\n> **Data notes**\n")
        assert "> - **FINRA daily short volume:** Daily, posted by 6 pm ET. Lookback is fixed" in r["prompt"]
        assert "> - **FINRA weekly dark pool (ATS) volume:**" in r["prompt"]
        chat = c.get(f"/api/sessions/{r['session_id']}").json()
        assert chat["next_turn_tool_scope"] == sources and chat["tool_scope"] is None  # skill's run only
        # A skill without sources: no callout, full team.
        plain = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        r2 = c.post(f"/api/skills/{plain['id']}/run", json={"params": {"ticker": "AAPL"}}).json()
        assert "Data notes" not in r2["prompt"]
        assert c.get(f"/api/sessions/{r2['session_id']}").json()["next_turn_tool_scope"] is None


def test_skill_draft_preselects_sources_used(monkeypatch):
    from app import skill_draft
    from tests.test_response_actions import answered_session

    async def fake_complete(model, system, user):
        return {"name": "NVDA check", "description": "", "prompt_template": "Check {ticker}",
                "params": [{"name": "ticker", "label": "Ticker", "default": ""}]}
    monkeypatch.setattr(skill_draft, "complete_json", fake_complete)
    with TestClient(app) as c:
        sid = answered_session(c, monkeypatch)  # the technical analyst called technical_snapshot
        draft = c.post(f"/api/sessions/{sid}/skill-draft").json()
        assert draft["tool_ids"] == [tools_by_slug(c)["technical_snapshot"]["id"]]


def test_connection_pricing_seeded_and_editable():
    with TestClient(app) as c:
        conns = {x["slug"]: x for x in c.get("/api/connections").json()}
        assert conns["yahoo"]["pricing"] == "free" and conns["massive"]["pricing"] == "paid"
        assert conns["ib_gateway"]["pricing"] == "account" and conns["finra"]["name"] == "FINRA"
        status = {s["id"]: s for s in c.get("/api/tools-status").json()}
        assert status[tools_by_slug(c)["massive_quote"]["id"]]["pricing"] == "paid"
        assert status[tools_by_slug(c)["fundamentals"]["id"]]["pricing"] == "free"
        r = c.put(f"/api/connections/{conns['massive']['id']}", json={"pricing": "free"})
        assert r.json()["pricing"] == "free"
    with TestClient(app) as c:  # your choice survives a restart
        conns = {x["slug"]: x for x in c.get("/api/connections").json()}
        assert conns["massive"]["pricing"] == "free"
        c.put(f"/api/connections/{conns['massive']['id']}", json={"pricing": "paid"})
