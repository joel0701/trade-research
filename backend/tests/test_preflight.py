"""Asking the user before running when data sources assigned to roles are unavailable."""
import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agents import loop
from app.db import engine
from app.main import app
from app.models import Message
from app.tools import ibkr_gateway
from tests.test_orchestrator import FakeClient


def events(c, sid, body):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json=body) as r:
        assert r.status_code == 200, r.read()
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def setup(monkeypatch):
    monkeypatch.setattr(loop, "client", lambda: FakeClient())
    monkeypatch.setattr(ibkr_gateway, "gateway_reachable", lambda timeout=0.3: False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

    async def fake_snapshot(ticker):
        return json.dumps({"ticker": ticker})
    from app.tools import python_tools
    monkeypatch.setattr(python_tools.PYTHON_TOOLS["technical_snapshot"], "fn", fake_snapshot)


def test_preflight_prompt_then_continue(monkeypatch):
    setup(monkeypatch)
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        ev = events(c, sid, {"text": "How does NVDA look?"})
        assert len(ev) == 1 and ev[0]["type"] == "confirm_needed"
        report = ev[0]
        items = {(i["role_slug"], i["tool"]): i for i in report["items"]}
        # Finance Analyst: EDGAR down and Bigdata.com (also SEC filings) not set up -> a real gap.
        sec = items[("finance_analyst", "SEC EDGAR filings")]
        assert sec["impact"] == "high" and "SEC filings" in sec["gaps"] and "SEC_USER_AGENT" in sec["reason"]
        # Technical Analyst: IBKR bars down but Yahoo price history works -> low impact, covered.
        bars = items[("technical_analyst", "IBKR price bars (IB Gateway)")]
        assert bars["impact"] == "low" and "Yahoo price history" in bars["covered_by"]
        assert "IB Gateway isn't running" in bars["reason"]
        # Sources on the disabled IBKR MCP connection are a deliberate choice: not reported.
        assert not any(t.startswith("IBKR real-time quotes") and "IB Gateway" not in t for _, t in items)
        assert report["items"][0]["impact"] == "high"  # high impact listed first
        with Session(engine) as db:  # nothing was saved
            assert not db.exec(select(Message).where(Message.session_id == sid)).all()

        ev = events(c, sid, {"text": "How does NVDA look?", "proceed": report["fingerprint"]})
        assert ev[-1]["type"] == "done"
        # Not remembered: the next question asks again.
        assert events(c, sid, {"text": "And AMD?"})[0]["type"] == "confirm_needed"


def test_preflight_remember_until_the_set_changes(monkeypatch):
    setup(monkeypatch)
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        fp = events(c, sid, {"text": "q1"})[0]["fingerprint"]
        assert events(c, sid, {"text": "q1", "proceed": fp, "remember": True})[-1]["type"] == "done"
        assert events(c, sid, {"text": "q2"})[-1]["type"] == "done"  # not asked again
        other = c.post("/api/sessions", json={}).json()["id"]
        assert events(c, other, {"text": "q"})[0]["type"] == "confirm_needed"  # per session

        monkeypatch.setenv("SEC_USER_AGENT", "TradeResearch test@example.com")  # set changes
        ev = events(c, sid, {"text": "q3"})
        assert ev[0]["type"] == "confirm_needed" and ev[0]["fingerprint"] != fp
        assert not any("EDGAR" in i["tool"] for i in ev[0]["items"])


def test_preflight_nothing_to_report(monkeypatch):
    setup(monkeypatch)
    from app import preflight
    monkeypatch.setattr(preflight, "availability", lambda tool, conn: (True, None))
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        assert events(c, sid, {"text": "q"})[-1]["type"] == "done"


def test_preflight_pending_prompt_and_decisions_are_recorded(monkeypatch):
    setup(monkeypatch)
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        fp = events(c, sid, {"text": "How does NVDA look?"})[0]["fingerprint"]
        # The held question survives leaving and coming back.
        pending = c.get(f"/api/sessions/{sid}").json()["pending_confirm"]
        assert pending["text"] == "How does NVDA look?" and pending["report"]["fingerprint"] == fp

        # Cancel: recorded in the conversation, prompt cleared, text handed back.
        assert c.post(f"/api/sessions/{sid}/pending/cancel").json() == {"text": "How does NVDA look?"}
        chat = c.get(f"/api/sessions/{sid}").json()
        assert chat["pending_confirm"] is None
        [note] = chat["messages"]
        assert note["role"] == "notice" and note["notice"]["decision"] == "cancelled"
        assert any(i["tool"] == "SEC EDGAR filings" and i["impact"] == "high" for i in note["notice"]["items"])

        # Continue and remember: a note precedes the question; the Lead is told what's missing.
        events(c, sid, {"text": "How does NVDA look?"})
        assert events(c, sid, {"text": "How does NVDA look?", "proceed": fp, "remember": True})[-1]["type"] == "done"
        msgs = c.get(f"/api/sessions/{sid}").json()["messages"]
        assert [m["role"] for m in msgs] == ["notice", "notice", "user", "assistant"]
        assert msgs[1]["notice"]["decision"] == "continued_remember"
        lead_call = next(k for k in fake.calls if "Your team" in k["system"])
        assert "unavailable for this question" in lead_call["system"] and "SEC EDGAR filings" in lead_call["system"]
        # Notices are never sent to the model.
        assert all(m["role"] in ("user", "assistant") for m in lead_call["messages"])

        # Later questions under "don't ask again" still get a note.
        fake.calls.clear()
        assert events(c, sid, {"text": "And AMD?"})[-1]["type"] == "done"
        msgs = c.get(f"/api/sessions/{sid}").json()["messages"]
        assert msgs[-3]["role"] == "notice" and msgs[-3]["notice"]["decision"] == "remembered"
        assert all(m["role"] in ("user", "assistant") for m in next(k for k in fake.calls if "Your team" in k["system"])["messages"])
