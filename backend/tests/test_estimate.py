"""Token and cost estimates for suggested next steps."""
from fastapi.testclient import TestClient

from app.estimate import scope_factor
from app.main import app
from tests.test_response_actions import answered_session


def test_scope_factor():
    assert scope_factor("What are the risks for NVDA?") == 1.0
    assert scope_factor("Compare NVDA, AMD and INTC") > 2
    assert scope_factor("Show the RSI and EPS trend for AAPL") == 1.0  # indicator names aren't tickers
    assert scope_factor("Compare " + ", ".join(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"])) == 3.0


def test_estimate_from_history(monkeypatch):
    with TestClient(app) as c:
        sid = answered_session(c, monkeypatch)
        r = c.post(f"/api/sessions/{sid}/estimate", json={"questions": ["Risks for NVDA?", "Compare NVDA, AMD, INTC"]})
        assert r.status_code == 200, r.text
        est = r.json()
        assert len(est["steps"]) == 2 and est["basis"]
        one, broad = est["steps"]
        assert one["tokens"] > 0 and broad["tokens"] > one["tokens"]
        assert one["cost"] is not None and one["cost_low"] < one["cost"] < one["cost_high"]
        assert c.post("/api/sessions/999999/estimate", json={"questions": ["x"]}).status_code == 404


def test_estimate_in_test_mode(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        est = c.post(f"/api/sessions/{sid}/estimate", json={"questions": ["a"]}).json()
        assert est["free"] and est["steps"][0]["cost"] == 0
