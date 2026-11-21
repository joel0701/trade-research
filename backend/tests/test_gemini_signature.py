"""Gemini 3+ thought signatures are sent back with tool calls; failed turns leave a notice in the chat."""
import json
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from app.agents import loop
from app.llm import openai_loop
from app.main import app
from tests.test_orchestrator import FakeClient
from tests.test_vendors import ask, ids

SIG = {"google": {"thought_signature": "sig-123"}}


def chunk(content=None, tool=None, finish=None):
    tcs = None
    if tool:
        tcs = [NS(index=0, id=tool[0], function=NS(name=tool[1], arguments=tool[2]), extra_content=tool[3])]
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs), finish_reason=finish)], usage=None)


class FakeGemini:
    """Like Gemini 3: rejects the request after a tool call unless the signature comes back."""

    def __init__(self):
        self.calls = []
        outer = self

        class Completions:
            async def create(self, **kw):
                outer.calls.append(kw)
                for m in kw["messages"]:
                    for tc in m.get("tool_calls") or []:
                        if tc.get("extra_content") != SIG:
                            raise RuntimeError("400 Function call is missing a thought_signature")
                return outer.stream(kw)

        self.chat = NS(completions=Completions())

    async def stream(self, kw):
        if not any(m["role"] == "tool" for m in kw["messages"]):
            for c in [chunk(tool=("c1", "consult_technical_analyst", '{"question": "NVDA close?"}', SIG)),
                      chunk(finish="tool_calls")]:
                yield c
        else:
            for c in [chunk(content="NVDA closed at 100."), chunk(finish="stop")]:
                yield c


def setup(monkeypatch, gemini):
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    claude = FakeClient()
    monkeypatch.setattr(openai_loop, "openai_client", lambda provider: gemini)
    monkeypatch.setattr(loop, "client", lambda: claude)

    async def fake_snapshot(ticker):
        return json.dumps({"ticker": ticker, "close": 100})
    from app.tools import python_tools
    monkeypatch.setattr(python_tools.PYTHON_TOOLS["technical_snapshot"], "fn", fake_snapshot)
    return claude


def test_signature_round_trip_and_switch_to_claude(monkeypatch):
    gemini = FakeGemini()
    claude = setup(monkeypatch, gemini)
    with TestClient(app) as c:
        t = ids(c)
        sid = c.post("/api/sessions", json={"lead_model_tool_id": t["gemini_flash"]}).json()["id"]
        events = ask(c, sid, "What is NVDA's closing price?")
        assert events[-1]["type"] == "done" and not [e for e in events if e["type"] == "error"], events
        assert c.get(f"/api/sessions/{sid}").json()["messages"][-1]["text"] == "NVDA closed at 100."

        # A later question with history from Gemini still sends the signature back...
        ask(c, sid, "And yesterday?")
        assert gemini.calls[-1]["messages"][2]["tool_calls"][0]["extra_content"] == SIG
        # ...and Claude never sees it.
        c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": t["opus"]})
        claude.calls.clear()
        ask(c, sid, "And the risks?")
        lead_call = next(k for k in claude.calls if "Your team" in k["system"])
        assert "vendor_extra" not in json.dumps(lead_call["messages"])


def test_failed_turn_leaves_error_notice(monkeypatch):
    class Broken(FakeGemini):
        async def stream(self, kw):
            raise RuntimeError("400 Bad request from Gemini")
            yield  # noqa
    setup(monkeypatch, Broken())
    with TestClient(app) as c:
        t = ids(c)
        sid = c.post("/api/sessions", json={"lead_model_tool_id": t["gemini_flash"]}).json()["id"]
        events = ask(c, sid, "What is NVDA's closing price?")
        assert any(e["type"] == "error" for e in events)
        notice = c.get(f"/api/sessions/{sid}").json()["messages"][-1]
        assert notice["role"] == "notice" and notice["notice"]["kind"] == "error"
        assert "Bad request" in notice["notice"]["message"] and notice["notice"]["model"] == "gemini-3.8-flash"
        # The notice is never sent to a model: asking again works with a clean history.
        assert c.get(f"/api/sessions/{sid}/runs").json()[0]["status"] == "error"
