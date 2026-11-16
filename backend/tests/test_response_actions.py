"""Answer footer actions: model shown per answer, feedback, and saving a conversation as a skill."""
import json

from fastapi.testclient import TestClient

from app import skill_draft
from app.agents import loop
from app.main import app
from tests.test_orchestrator import FakeClient


def answered_session(c, monkeypatch):
    monkeypatch.setattr(loop, "client", lambda: FakeClient())

    async def fake_snapshot(ticker):
        return json.dumps({"ticker": ticker, "close": 100})
    from app.tools import python_tools
    monkeypatch.setattr(python_tools.PYTHON_TOOLS["technical_snapshot"], "fn", fake_snapshot)
    sid = c.post("/api/sessions", json={}).json()["id"]
    with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": "How does NVDA look?"}) as r:
        list(r.iter_lines())
    return sid


def test_answers_carry_model_and_feedback(monkeypatch):
    with TestClient(app) as c:
        sid = answered_session(c, monkeypatch)
        answer = c.get(f"/api/sessions/{sid}").json()["messages"][-1]
        assert answer["role"] == "assistant" and answer["model"] == "claude-opus-5-5"
        assert answer["feedback"] is None

        url = f"/api/sessions/{sid}/messages/{answer['id']}/feedback"
        assert c.put(url, json={"rating": "up"}).json()["feedback"] == "up"
        assert c.get(f"/api/sessions/{sid}").json()["messages"][-1]["feedback"] == "up"
        assert c.put(url, json={"rating": None}).json()["feedback"] is None
        assert c.put(url, json={"rating": "meh"}).status_code == 422
        question = c.get(f"/api/sessions/{sid}").json()["messages"][0]
        assert c.put(f"/api/sessions/{sid}/messages/{question['id']}/feedback", json={"rating": "up"}).status_code == 404


def test_skill_draft_is_normalized_and_saveable(monkeypatch):
    seen = {}

    async def fake_complete(model, system, user):
        seen["model"], seen["user"] = model.model_id, user
        # Messy draft: odd param name, a placeholder with no param, an unused param, stray braces.
        return {"name": "Full Stock Analysis", "description": "Complete view of a stock.",
                "prompt_template": "Analyse {Ticker Symbol} over {horizon}. Return JSON like {\"a\": 1}.",
                "params": [{"name": "Ticker Symbol", "label": "Ticker", "default": ""},
                           {"name": "unused", "label": "Unused", "default": "x"}]}

    monkeypatch.setattr(skill_draft, "complete_json", fake_complete)
    with TestClient(app) as c:
        sid = answered_session(c, monkeypatch)
        draft = c.post(f"/api/sessions/{sid}/skill-draft").json()
        assert seen["model"] == "claude-sonnet-5-5"  # preferred drafting model
        assert "User: How does NVDA look?" in seen["user"] and "Analyst: NVDA is in an uptrend." in seen["user"]
        assert draft["slug"] == "full_stock_analysis"
        assert [p["name"] for p in draft["params"]] == ["ticker_symbol", "horizon"]
        assert draft["prompt_template"].startswith("Analyse {ticker_symbol} over {horizon}.")
        # The template renders with str.format (stray braces escaped).
        assert draft["prompt_template"].format(ticker_symbol="AAPL", horizon="3 months").endswith('{"a": 1}.')

        saved = c.post("/api/skills", json={k: v for k, v in draft.items() if k != "drafted_with"}).json()
        run = c.post(f"/api/skills/{saved['id']}/run", json={"params": {"ticker_symbol": "AAPL", "horizon": "1y"}}).json()
        assert run["prompt"].startswith("Analyse AAPL over 1y.")
        # Drafting again can't collide with the saved skill's slug.
        assert c.post(f"/api/sessions/{sid}/skill-draft").json()["slug"] == "full_stock_analysis_2"

        empty = c.post("/api/sessions", json={}).json()["id"]
        assert c.post(f"/api/sessions/{empty}/skill-draft").status_code == 422


def test_fork_copies_conversation_up_to_a_message(monkeypatch):
    with TestClient(app) as c:
        sid = answered_session(c, monkeypatch)
        att = c.post(f"/api/sessions/{sid}/attachments", files={"file": ("notes.md", b"Rev +12%", "text/markdown")}).json()
        with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": "With my notes?", "attachment_ids": [att["id"]]}) as r:
            list(r.iter_lines())
        with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": "Third question"}) as r:
            list(r.iter_lines())
        msgs = c.get(f"/api/sessions/{sid}").json()["messages"]
        assert len(msgs) == 6
        second_answer = msgs[3]

        f = c.post(f"/api/sessions/{sid}/fork", json={"message_id": second_answer["id"]}).json()
        assert f["origin"] == f"fork:{sid}" and f["title"].endswith("(fork)")
        forked = c.get(f"/api/sessions/{f['id']}").json()["messages"]
        assert [m["text"] for m in forked] == [m["text"] for m in msgs[:4]]
        # Trace and attachments came along and belong to the fork.
        runs = c.get(f"/api/sessions/{f['id']}/runs").json()
        assert {r["role_slug"] for r in runs} == {"lead", "technical_analyst"} and len([r for r in runs if r["role_slug"] == "lead"]) == 2
        assert forked[3]["run_id"] in {r["id"] for r in runs}
        new_att = forked[2]["attachments"][0]
        assert new_att["id"] != att["id"]
        assert c.get(f"/api/sessions/{f['id']}/attachments/{new_att['id']}").content == b"Rev +12%"

        # Deleting the original leaves the fork intact, and the fork can continue.
        c.delete(f"/api/sessions/{sid}")
        assert c.get(f"/api/sessions/{f['id']}/attachments/{new_att['id']}").content == b"Rev +12%"
        with c.stream("POST", f"/api/sessions/{f['id']}/messages", json={"text": "A different direction"}) as r:
            assert list(r.iter_lines())
        assert len(c.get(f"/api/sessions/{f['id']}").json()["messages"]) == 6
        assert c.post(f"/api/sessions/{f['id']}/fork", json={"message_id": 999999}).status_code == 404
