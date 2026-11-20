from fastapi.testclient import TestClient

from app.main import app


def test_seeded_config_and_crud():
    with TestClient(app) as c:
        roles = c.get("/api/team").json()
        assert {r["slug"] for r in roles} >= {"lead", "technical_analyst", "strategy_optimizer"}
        assert sum(r["is_lead"] for r in roles) == 1
        tools = {t["slug"]: t for t in c.get("/api/tools").json()}
        conns = {x["slug"]: x for x in c.get("/api/connections").json()}
        # One IB Gateway connection backs several sources.
        gw = ("ibkr_gw_quote", "ibkr_gw_options", "ibkr_gw_account", "ibkr_gw_scanner")
        assert {tools[s]["connection_id"] for s in gw} == {conns["ib_gateway"]["id"]}
        assert tools["ibkr_gw_account"]["capabilities"] == ["brokerage"]
        assert tools["ibkr_gw_scanner"]["capabilities"] == ["screening"]
        assert "ibkr" not in conns and not any(t.startswith("ibkr_") and not t.startswith("ibkr_gw") for t in tools)
        assert tools["insider_trades"]["capabilities"] == ["insider_trading"]

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
        assert "**NVDA**" in body["prompt"] and "**bullish**" in body["prompt"]  # entries (and defaults) in bold
        chat = c.get(f"/api/sessions/{body['session_id']}").json()
        assert chat["origin"] == f"skill:{skill['id']}"
        missing = c.post(f"/api/skills/{skill['id']}/run", json={"params": {}})
        assert missing.status_code == 422


def test_rename_and_delete_session():
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        assert c.patch(f"/api/sessions/{sid}", json={"title": "NVDA earnings"}).json()["title"] == "NVDA earnings"
        assert any(s["id"] == sid and s["title"] == "NVDA earnings" for s in c.get("/api/sessions").json())
        assert c.delete(f"/api/sessions/{sid}").json() == {"ok": True}
        assert c.get(f"/api/sessions/{sid}").status_code == 404
        assert all(s["id"] != sid for s in c.get("/api/sessions").json())
        assert c.patch(f"/api/sessions/{sid}", json={"title": "  "}).status_code == 404  # already deleted


def test_rename_rejects_blank_title():
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        assert c.patch(f"/api/sessions/{sid}", json={"title": "   "}).status_code == 422


def test_describe_rules_in_words():
    from app.api.launch import describe_rules
    assert describe_rules({}) == ""
    assert describe_rules({"all": [{"field": "close", "op": ">", "field2": "sma_200"},
                                   {"any": [{"field": "rsi_14", "op": "<", "value": 30},
                                            {"field": "close", "op": "between", "value": [10, 20]}]}]}) == \
        "close > sma_200 AND (rsi_14 < 30 OR close between 10 and 20)"
    assert describe_rules({"not": {"field": "rel_volume", "op": ">=", "value": 2}}) == "NOT (rel_volume >= 2)"
