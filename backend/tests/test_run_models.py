"""A scanner's or skill's model runs only its first answer; follow-ups use the Research default."""
import json

from fastapi.testclient import TestClient

from app.agents import loop
from app.main import app
from app.tools import ibkr_gateway
from tests.test_orchestrator import FakeClient


def send(c, sid, body):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json=body) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def lead_models(fake):
    return [k["model"] for k in fake.calls if "Your team" in k["system"]]


def test_scanner_model_for_analysis_then_default(monkeypatch):
    from app.screeners import engine

    async def fake_scan(**kw):
        return [{"rank": 1, "ticker": "CRWV"}]
    monkeypatch.setattr(ibkr_gateway, "run_scan", fake_scan)
    monkeypatch.setattr(engine, "fetch_snapshots", lambda t: {})
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        tools = {t["slug"]: t for t in c.get("/api/tools").json()}
        sonnet, opus = tools["sonnet"], tools["opus"]
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "ibkr_top_gainers")
        c.put(f"/api/screeners/{sc['id']}", json={"lead_model_tool_id": sonnet["id"]})
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        assert send(c, r["session_id"], {"text": r["prompt"], "hidden": True})[-1]["type"] == "done"
        first = len(lead_models(fake))
        assert set(lead_models(fake)) == {sonnet["config"]["model_id"]}
        send(c, r["session_id"], {"text": "Which of these has the best catalyst?"})
        assert set(lead_models(fake)[first:]) == {opus["config"]["model_id"]}  # the Lead role's default (Research)
        msgs = [m for m in c.get(f"/api/sessions/{r['session_id']}").json()["messages"] if m["role"] == "assistant" and m.get("run_id")]
        assert msgs[0]["model"] == sonnet["config"]["model_id"] and msgs[-1]["model"] == opus["config"]["model_id"]
        c.put(f"/api/screeners/{sc['id']}", json={"lead_model_tool_id": None})


def test_skill_model_for_first_answer_only(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        tools = {t["slug"]: t for t in c.get("/api/tools").json()}
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        r = c.post(f"/api/skills/{skill['id']}/run",
                   json={"params": {"ticker": "AAPL"}, "lead_model_tool_id": tools["haiku"]["id"]}).json()
        send(c, r["session_id"], {"text": r["prompt"]})
        n = len(lead_models(fake))
        send(c, r["session_id"], {"text": "And the risks?"})
        assert set(lead_models(fake)[:n]) == {tools["haiku"]["config"]["model_id"]}
        assert set(lead_models(fake)[n:]) == {tools["opus"]["config"]["model_id"]}
