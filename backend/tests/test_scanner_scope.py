"""Scanner sessions use only the scanner's data sources; EDGAR contact email from Settings."""
import json

from fastapi.testclient import TestClient

from app.agents import loop
from app.main import app
from app.tools import ibkr_gateway, sec_edgar
from tests.test_orchestrator import FakeClient


def ids(c):
    return {t["slug"]: t["id"] for t in c.get("/api/tools").json()}


def events(c, sid, body):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json=body) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_scanner_session_preflight_only_checks_its_sources(monkeypatch):
    monkeypatch.setattr(ibkr_gateway, "gateway_reachable", lambda timeout=0.3: True)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("MASSIVE_API_KEY", "mk")
    from app.screeners import engine

    async def fake_scan(**kw):
        return [{"rank": 1, "ticker": "CRWV", "name": "CoreWeave"}]
    monkeypatch.setattr(ibkr_gateway, "run_scan", fake_scan)
    monkeypatch.setattr(engine, "fetch_snapshots", lambda t: {})
    monkeypatch.setattr(loop, "client", lambda: FakeClient())
    with TestClient(app) as c:
        t = ids(c)
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "ibkr_top_gainers")
        assert sc["tool_ids"] and t["sec_filings"] not in sc["tool_ids"]  # defaults are explicit, no EDGAR
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        chat = c.get(f"/api/sessions/{r['session_id']}").json()
        assert chat["tool_scope"] == sc["tool_ids"]
        # EDGAR isn't set up, but it isn't one of this scanner's sources: no prompt, the run goes ahead.
        ev = events(c, r["session_id"], {"text": r["prompt"]})
        assert ev[0]["type"] != "confirm_needed" and ev[-1]["type"] == "done"

        # Lifting the limit (Use full team) brings the full check back.
        c.patch(f"/api/sessions/{r['session_id']}", json={"tool_scope": None})
        ev = events(c, r["session_id"], {"text": "Now the fundamentals?"})
        assert ev[0]["type"] == "confirm_needed" and any("EDGAR" in i["tool"] for i in ev[0]["items"])


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
            assert tool_names == {"consult_technical_analyst", "consult_strategy_optimizer", "finra_ats_weekly"}
            assert "limited to these data sources" in lead_call["system"]
            spec_call = next(k for k in fake.calls if k["model"] == "claude-sonnet-5-5")
            assert {d["name"] for d in spec_call["tools"]} == {"technical_snapshot"}
            # Role settings themselves are untouched.
            assert next(r for r in c.get("/api/roles").json() if r["slug"] == "technical_analyst")["tool_ids"]
        finally:
            c.put(f"/api/roles/{tech['id']}", json={"tool_ids": tech["tool_ids"]})


def test_edgar_email_from_settings(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with TestClient(app) as c:
        conn = next(x for x in c.get("/api/connections").json() if x["slug"] == "sec_edgar")
        assert conn["config"]["needs_contact_email"] and conn["secret_env"] is None
        sec_id = ids(c)["sec_filings"]
        status = lambda: {s["id"]: s for s in c.get("/api/tools-status").json()}[sec_id]  # noqa: E731
        assert not status()["available"] and "Settings" in status()["reason"]
        assert sec_edgar.user_agent() is None

        c.put(f"/api/connections/{conn['id']}", json={"config": {**conn["config"], "contact_email": "not-an-email"}})
        assert sec_edgar.user_agent() is None
        c.put(f"/api/connections/{conn['id']}", json={"config": {**conn["config"], "contact_email": "joe@example.com"}})
        assert sec_edgar.user_agent() == "TradeResearch joe@example.com" and status()["available"]

        monkeypatch.setenv("SEC_USER_AGENT", "MyApp other@example.com")  # .env wins when set
        assert sec_edgar.user_agent() == "MyApp other@example.com"
        c.put(f"/api/connections/{conn['id']}", json={"config": conn["config"]})  # tidy up
