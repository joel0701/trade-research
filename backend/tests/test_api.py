from fastapi.testclient import TestClient

from app.main import app


def test_seeded_config_and_crud():
    with TestClient(app) as c:
        roles = c.get("/api/roles").json()
        assert {r["slug"] for r in roles} >= {"lead", "technical_analyst", "strategy_optimizer"}
        assert sum(r["is_lead"] for r in roles) == 1
        tools = c.get("/api/tools").json()
        ibkr = next(t for t in tools if t["slug"] == "ibkr")
        assert not ibkr["enabled"] and "create_order_instruction" in ibkr["config"]["blocked_tools"]

        new = c.post("/api/tools", json={"slug": "x", "name": "X", "kind": "python",
                                         "config": {"function": "fundamentals"}}).json()
        upd = c.put(f"/api/tools/{new['id']}", json={"enabled": False}).json()
        assert upd["enabled"] is False and upd["config"] == {"function": "fundamentals"}
        assert c.delete(f"/api/tools/{new['id']}").json() == {"ok": True}


def test_skill_run_creates_session():
    with TestClient(app) as c:
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "options_strategy")
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "NVDA"}})
        assert r.status_code == 200
        body = r.json()
        assert "NVDA" in body["prompt"] and "bullish" in body["prompt"]  # default filled in
        chat = c.get(f"/api/sessions/{body['session_id']}").json()
        assert chat["origin"] == f"skill:{skill['id']}"
        missing = c.post(f"/api/skills/{skill['id']}/run", json={"params": {}})
        assert missing.status_code == 422
