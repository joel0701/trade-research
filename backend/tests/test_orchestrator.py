"""End-to-end Lead -> specialist -> tool flow against a fake Claude client."""
import json

from anthropic.types.beta import BetaMessage
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agents import loop
from app.agents.orchestrator import repair_history
from app.db import engine
from app.main import app
from app.models import Message, Run


def msg(content, stop_reason, model="claude-opus-5-5"):
    return BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


class FakeStream:
    def __init__(self, message):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for b in self.message.content:
                if b.type == "text":
                    yield type("E", (), {"type": "content_block_delta",
                                         "delta": type("D", (), {"type": "text_delta", "text": b.text})()})()
        return gen()

    async def get_final_message(self):
        return self.message


class FakeClient:
    """Lead asks the technical analyst; the analyst calls a tool; both then answer."""

    def __init__(self):
        self.calls = []
        outer = self

        class Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return FakeStream(outer.respond(kw))

        self.beta = type("B", (), {"messages": Messages()})()

    def respond(self, kw):
        is_lead = "Your team" in kw["system"]
        last = kw["messages"][-1]["content"]
        got_results = isinstance(last, list) and any(b.get("type") == "tool_result" for b in last)
        if is_lead and not got_results:
            return msg([{"type": "text", "text": "Checking the chart."},
                        {"type": "tool_use", "id": "tu_1", "name": "consult_technical_analyst",
                         "input": {"question": "Trend for NVDA?"}}], "tool_use")
        if is_lead:
            return msg([{"type": "text", "text": "NVDA is in an uptrend."}], "end_turn")
        if not got_results:
            return msg([{"type": "tool_use", "id": "tu_2", "name": "technical_snapshot",
                         "input": {"ticker": "NVDA"}}], "tool_use", "claude-sonnet-5-5")
        return msg([{"type": "text", "text": "Above 50 and 200 day averages."}], "end_turn", "claude-sonnet-5-5")


def test_full_turn(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)

    async def fake_snapshot(ticker):
        return json.dumps({"ticker": ticker, "close": 100})

    from app.tools import python_tools
    monkeypatch.setattr(python_tools.PYTHON_TOOLS["technical_snapshot"], "fn", fake_snapshot)

    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": "How does NVDA look?"}) as r:
            events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
        types = [e["type"] for e in events]
        assert "specialist_start" in types and "specialist_end" in types and types[-1] == "done", events

        chat = c.get(f"/api/sessions/{sid}").json()
        assert chat["title"] == "How does NVDA look?"
        assert [m["text"] for m in chat["messages"]] == [
            "How does NVDA look?", "Checking the chart.", "NVDA is in an uptrend."]

        runs = c.get(f"/api/sessions/{sid}/runs").json()
        spec = next(r for r in runs if r["role_slug"] == "technical_analyst")
        assert spec["status"] == "ok" and spec["tool_calls"][0]["name"] == "technical_snapshot"
        lead_run = next(r for r in runs if r["role_slug"] == "lead")
        assert spec["parent_run_id"] == lead_run["id"]
        assert lead_run["model"] == "claude-opus-5-5" and spec["model"] == "claude-sonnet-5-5"
        assert lead_run["usage"]["input_tokens"] > 0 and spec["finished_at"]

    # Lead used Opus with thinking + fallbacks; the specialist used Sonnet.
    lead_call = next(k for k in fake.calls if "Your team" in k["system"])
    assert lead_call["model"] == "claude-opus-5-5" and lead_call["fallbacks"] == "default"
    assert lead_call["thinking"]["type"] == "adaptive"
    assert any(k["model"] == "claude-sonnet-5-5" for k in fake.calls)

    # Full history (incl. hidden tool plumbing) is persisted for continuation.
    with Session(engine) as db:
        rows = db.exec(select(Message).where(Message.session_id == sid).order_by(Message.id)).all()
        assert [m.role for m in rows] == ["user", "assistant", "user", "assistant"]
        assert rows[2].visible is False
        assert db.exec(select(Run).where(Run.session_id == sid)).all()


def test_repair_history_drops_unanswered_tool_use():
    h = [{"role": "user", "content": [{"type": "text", "text": "q"}]},
         {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "t", "input": {}}]}]
    assert repair_history(h) == h[:1]
