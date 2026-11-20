"""Test mode: the whole flow runs with canned responses and no model calls or keys."""
import json

from fastapi.testclient import TestClient

from app.agents import loop
from app.llm import openai_loop
from app.llm.mock_loop import guess_ticker
from app.main import app


def no_real_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a real LLM client was used in mock mode")
    monkeypatch.setattr(loop, "client", boom)
    monkeypatch.setattr(openai_loop, "openai_client", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def fake_snapshot(ticker):
        return json.dumps({"ticker": ticker, "close": 123.4, "rsi_14": 55})
    from app.tools import python_tools
    for name in ("technical_snapshot", "price_history", "fundamentals", "options_chain"):
        monkeypatch.setattr(python_tools.PYTHON_TOOLS[name], "fn", lambda ticker, **k: fake_snapshot(ticker))


def ask(c, sid, text):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": text}) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_mock_mode_runs_full_flow_for_free(monkeypatch):
    no_real_llm(monkeypatch)
    monkeypatch.setenv("MOCK_LLM", "1")
    with TestClient(app) as c:
        assert c.get("/api/health").json()["mock_llm"] is True
        sid = c.post("/api/sessions", json={}).json()["id"]
        events = ask(c, sid, "How does NVDA look technically?")
        types = [e["type"] for e in events]
        assert types[-1] == "done" and "specialist_start" in types and "text" in types, types
        msgs = c.get(f"/api/sessions/{sid}").json()["messages"]
        answer = msgs[-1]
        assert answer["role"] == "assistant" and answer["model"] == "mock" and "Test mode response" in answer["text"]
        assert "NVDA" in answer["text"]
        runs = c.get(f"/api/sessions/{sid}/runs").json()
        assert all(r["model"] == "mock" and r["status"] == "ok" for r in runs) and len(runs) >= 2
        assert all(r["usage"].get("input_tokens", 0) == 0 for r in runs)
        # The "technically" question went to the Technical Analyst, which really called a data tool.
        tech = next(r for r in runs if r["role_slug"] == "technical_analyst")
        assert tech["tool_calls"] and tech["tool_calls"][0]["name"] in {"price_history", "technical_snapshot"}
        assert tech["tool_calls"][0]["input"] == {"ticker": "NVDA"} and "123.4" in answer["text"]
        # Follow-ups work too.
        assert ask(c, sid, "And AMD?")[-1]["type"] == "done"


def test_mock_model_can_be_chosen_per_session_without_key(monkeypatch):
    no_real_llm(monkeypatch)
    monkeypatch.delenv("MOCK_LLM", raising=False)
    with TestClient(app) as c:
        mock = next(m for m in c.get("/api/models").json() if m["slug"] == "mock")
        assert mock["available"] and mock["provider_name"] == "Mock (no cost)"
        sid = c.post("/api/sessions", json={"lead_model_tool_id": mock["id"]}).json()["id"]
        # Specialists use their own (real) models, so give them nothing to do: patch them to the mock too.
        monkeypatch.setenv("MOCK_LLM", "1")
        assert ask(c, sid, "Quick look at AAPL")[-1]["type"] == "done"


def test_mock_skill_draft(monkeypatch):
    no_real_llm(monkeypatch)
    monkeypatch.setenv("MOCK_LLM", "1")
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        ask(c, sid, "Give me a full analysis of CRWV")
        d = c.post(f"/api/sessions/{sid}/skill-draft").json()
        assert d["drafted_with"] == "mock" and "{ticker}" in d["prompt_template"]
        assert d["params"] == [{"name": "ticker", "label": "Ticker", "default": ""}]


def test_guess_ticker():
    assert guess_ticker("What is the dark pool positioning for CRWV?") == "CRWV"
    assert guess_ticker("Run scanner **IBKR: top % gainers** RSI and ETF") == "SPY"
    assert guess_ticker("I am bullish on **SNDK** over the next 2-4 weeks") == "SNDK"
