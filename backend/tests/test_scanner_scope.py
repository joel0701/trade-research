"""Scanner sessions: the scanner defines only the scan; the analysis works like any research session."""
import json

from fastapi.testclient import TestClient

from app.agents import loop
from app.main import app
from app.tools import ibkr_gateway
from tests.test_orchestrator import FakeClient


def ids(c):
    return {t["slug"]: t["id"] for t in c.get("/api/tools").json()}


def events(c, sid, body):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json=body) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_scanner_session_questions_use_full_team_and_preflight_like_research(monkeypatch):
    monkeypatch.setattr(ibkr_gateway, "gateway_reachable", lambda timeout=0.3: True)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("MASSIVE_API_KEY", "mk")
    from app.screeners import engine

    async def fake_scan(**kw):
        return [{"rank": 1, "ticker": "CRWV", "name": "CoreWeave"}]
    monkeypatch.setattr(ibkr_gateway, "run_scan", fake_scan)
    monkeypatch.setattr(engine, "fetch_snapshots", lambda t: {})
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "ibkr_top_gainers")
        assert "tool_ids" not in sc  # a scanner configures only the scan
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        chat = c.get(f"/api/sessions/{r['session_id']}").json()
        assert chat["tool_scope"] is None and fake.calls == []  # results only: nothing ran yet
        # Your question is checked like any research question: EDGAR isn't set up, so it asks first.
        ev = events(c, r["session_id"], {"text": "What's driving CRWV?"})
        assert ev[0]["type"] == "confirm_needed" and any("EDGAR" in i["tool"] for i in ev[0]["items"])
        ev = events(c, r["session_id"], {"text": "What's driving CRWV?", "proceed": ev[0]["fingerprint"]})
        assert ev[-1]["type"] == "done"
        msgs = c.get(f"/api/sessions/{r['session_id']}").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "notice", "user", "assistant"]
        assert msgs[0]["text"].startswith("Run scanner **IBKR: top % gainers**") and msgs[1]["model"] == "IBKR scanner"
        # The Lead saw the scan results before your question, and had the whole team.
        lead = next(k for k in fake.calls if "Your team" in k["system"])
        assert "| rank |" in lead["messages"][1]["content"][0]["text"]
        assert {"consult_finance_analyst", "consult_news_analyst"} <= {t["name"] for t in lead["tools"]}


def test_old_scanner_sessions_lose_their_limit():
    from sqlmodel import Session
    from app.db import engine as db_engine
    from app.models import ChatSession
    from app.seed import ensure_scanners
    with Session(db_engine) as db:
        chat = ChatSession(title="old scan", origin="screener:1", tool_scope=[1, 2])
        db.add(chat)
        db.commit()
        ensure_scanners(db)
        db.refresh(chat)
        assert chat.tool_scope is None


def test_scope_limits_team_and_gives_unheld_sources_to_lead(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    monkeypatch.setattr(ibkr_gateway, "gateway_reachable", lambda timeout=0.3: True)

    async def fake_snapshot(ticker):
        return json.dumps({"ticker": ticker})
    from app.tools import python_tools
    monkeypatch.setattr(python_tools.PYTHON_TOOLS["technical_snapshot"], "fn", fake_snapshot)
    with TestClient(app) as c:
        t = ids(c)
        # technical_snapshot is held by the Technical Analyst; finra_ats_weekly by nobody else here -> Lead.
        tech = next(r for r in c.get("/api/roles").json() if r["slug"] == "technical_analyst")
        c.put(f"/api/roles/{tech['id']}", json={"tool_ids": [x for x in tech["tool_ids"] if x != t["finra_ats_weekly"]]})
        try:
            sid = c.post("/api/sessions", json={}).json()["id"]
            c.patch(f"/api/sessions/{sid}", json={"tool_scope": [t["technical_snapshot"], t["finra_ats_weekly"]]})
            assert events(c, sid, {"text": "How does NVDA look?"})[-1]["type"] == "done"
            lead_call = next(k for k in fake.calls if "Your team" in k["system"])
            tool_names = {d["name"] for d in lead_call["tools"]}
            # Only specialists holding a scoped source (both hold Yahoo technical snapshot); FINRA ATS -> Lead.
            assert tool_names - {"read_more"} == {"consult_technical_analyst", "consult_strategy_optimizer", "finra_ats_weekly"}
            assert "limited to these data sources" in lead_call["system"]
            spec_call = next(k for k in fake.calls if k["model"] == "claude-sonnet-5-5")
            assert {d["name"] for d in spec_call["tools"]} - {"read_more"} == {"technical_snapshot"}
            # Role settings themselves are untouched.
            assert next(r for r in c.get("/api/roles").json() if r["slug"] == "technical_analyst")["tool_ids"]
        finally:
            c.put(f"/api/roles/{tech['id']}", json={"tool_ids": tech["tool_ids"]})


def test_edgar_is_configured_in_env_only(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with TestClient(app) as c:
        conn = next(x for x in c.get("/api/connections").json() if x["slug"] == "sec_edgar")
        assert conn["secret_env"] == "SEC_USER_AGENT" and "needs_contact_email" not in conn["config"]
        sec_id = ids(c)["sec_filings"]
        status = lambda: {s["id"]: s for s in c.get("/api/tools-status").json()}[sec_id]  # noqa: E731
        assert status() == {"id": sec_id, "available": False, "reason": "set SEC_USER_AGENT in .env", "pricing": "free"}
        monkeypatch.setenv("SEC_USER_AGENT", "TradeResearch joe@example.com")
        assert status()["available"]
