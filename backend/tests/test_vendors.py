"""Multiple LLM vendors: key gating, the OpenAI-compatible loop, and switching mid-session."""
import base64
import io
import json
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from app.agents import loop
from app.llm import openai_loop
from app.llm.convert import to_openai_messages
from app.main import app
from tests.test_orchestrator import FakeClient


def chunk(content=None, tool=None, finish=None, usage=None):
    tcs = [NS(index=tool[0], id=tool[1], function=NS(name=tool[2], arguments=tool[3]))] if tool else None
    choices = [NS(delta=NS(content=content, tool_calls=tcs), finish_reason=finish)] if (content or tool or finish) else []
    return NS(choices=choices, usage=usage)


class FakeOpenAI:
    """GPT as Lead: consults the technical analyst (arguments streamed in pieces), then answers."""

    def __init__(self):
        self.calls = []
        outer = self

        class Completions:
            async def create(self, **kw):
                outer.calls.append(kw)
                return outer.stream(kw)

        self.chat = NS(completions=Completions())

    async def stream(self, kw):
        has_results = any(m["role"] == "tool" for m in kw["messages"])
        if not has_results:
            for c in [chunk(content="Let me check. "),
                      chunk(tool=(0, "call.abc/1", "consult_technical_analyst", '{"quest')),
                      chunk(tool=(0, None, None, 'ion": "Trend for NVDA?"}')),
                      chunk(finish="tool_calls"),
                      chunk(usage=NS(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=None))]:
                yield c
        else:
            for c in [chunk(content="NVDA trend "), chunk(content="is up."), chunk(finish="stop"),
                      chunk(usage=NS(prompt_tokens=150, completion_tokens=8,
                                     prompt_tokens_details=NS(cached_tokens=50)))]:
                yield c


def ids(c):
    return {t["slug"]: t["id"] for t in c.get("/api/tools").json()}


def ask(c, sid, text):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": text}) as r:
        assert r.status_code == 200, r.read()
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_only_models_with_keys_are_available(monkeypatch):
    with TestClient(app) as c:
        models = {m["slug"]: m for m in c.get("/api/models").json()}
        assert models["opus"]["available"] and models["opus"]["provider"] == "anthropic"
        assert not models["gpt"]["available"] and models["gpt"]["key_env"] == "OPENAI_API_KEY"
        assert "sk-" not in json.dumps(models)  # keys are never returned
        provs = {p["slug"]: p for p in c.get("/api/providers").json()}
        assert provs["anthropic"]["configured"] and not provs["xai"]["configured"]

        sid = c.post("/api/sessions", json={}).json()["id"]
        r = c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": models["gpt"]["id"]})
        assert r.status_code == 422 and "OPENAI_API_KEY" in r.json()["detail"]

        monkeypatch.setenv("OPENAI_API_KEY", "test")
        assert {m["slug"]: m for m in c.get("/api/models").json()}["gpt"]["available"]
        assert c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": models["gpt"]["id"]}).status_code == 200


def test_gpt_lead_then_switch_to_claude(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    gpt, claude = FakeOpenAI(), FakeClient()
    monkeypatch.setattr(openai_loop, "openai_client", lambda provider: gpt)
    monkeypatch.setattr(loop, "client", lambda: claude)

    async def fake_snapshot(ticker):
        return json.dumps({"ticker": ticker, "close": 100})
    from app.tools import python_tools
    monkeypatch.setattr(python_tools.PYTHON_TOOLS["technical_snapshot"], "fn", fake_snapshot)

    with TestClient(app) as c:
        t = ids(c)
        sid = c.post("/api/sessions", json={"lead_model_tool_id": t["gpt"]}).json()["id"]
        events = ask(c, sid, "How does NVDA look?")
        assert [e["type"] for e in events][-1] == "done", events

        first = gpt.calls[0]
        assert first["model"] == "gpt-5" and first["max_completion_tokens"] == 32000
        assert first["messages"][0]["role"] == "system" and "Your team" in first["messages"][0]["content"]
        names = {tool["function"]["name"] for tool in first["tools"]}
        assert "consult_technical_analyst" in names
        # The specialist (Claude Sonnet) really ran, with the streamed-in-pieces arguments.
        assert any(k["model"] == "claude-sonnet-5-5" and k["messages"][0]["content"] == "Trend for NVDA?"
                   for k in claude.calls)

        chat = c.get(f"/api/sessions/{sid}").json()
        assert [m["text"] for m in chat["messages"]] == ["How does NVDA look?", "NVDA trend is up."]
        lead = next(r for r in c.get(f"/api/sessions/{sid}/runs").json() if r["role_slug"] == "lead")
        assert lead["model"] == "gpt-5"
        assert lead["usage"] == {"input_tokens": 200, "cache_read_input_tokens": 50, "output_tokens": 28}

        # Switch the same session to Claude: the GPT turn's history must be valid for Claude.
        c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": t["opus"]})
        claude.calls.clear()
        ask(c, sid, "And the risks?")
        lead_call = next(k for k in claude.calls if "Your team" in k["system"])
        assert lead_call["model"] == "claude-opus-5-5"
        hist = lead_call["messages"]
        tool_use = next(b for m in hist if m["role"] == "assistant" for b in m["content"] if b["type"] == "tool_use")
        tool_result = next(b for m in hist if m["role"] == "user" for b in m["content"] if b.get("type") == "tool_result")
        assert tool_use["id"] == tool_result["tool_use_id"] == "call_abc_1"  # sanitized, consistent


def test_missing_key_for_specialist_fails_gracefully(monkeypatch):
    claude = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: claude)
    with TestClient(app) as c:
        t = ids(c)
        role = next(r for r in c.get("/api/team").json() if r["slug"] == "technical_analyst")
        c.put(f"/api/team/{role['id']}", json={"model_tool_id": t["grok"]})  # no XAI key
        try:
            sid = c.post("/api/sessions", json={}).json()["id"]
            events = ask(c, sid, "How does NVDA look?")
            assert events[-1]["type"] == "done"
            spec = next(r for r in c.get(f"/api/sessions/{sid}/runs").json() if r["role_slug"] == "technical_analyst")
            assert spec["status"] == "error" and "XAI_API_KEY" in spec["error"]
        finally:
            c.put(f"/api/team/{role['id']}", json={"model_tool_id": t["sonnet"]})


def test_claude_history_converts_for_other_vendors():
    from pypdf import PdfWriter
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=72, height=72)
    w.write(buf)
    pdf_b64 = base64.b64encode(buf.getvalue()).decode()
    hist = [
        {"role": "user", "content": [
            {"type": "document", "title": "notes.md", "source": {"type": "text", "media_type": "text/plain", "data": "Rev +12%"}},
            {"type": "document", "title": "scan.pdf", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            {"type": "text", "text": "Summarise"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "x"},
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_1", "name": "fundamentals", "input": {"ticker": "EQT"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "pe 12", "is_error": False}]},
        {"role": "assistant", "content": [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}},
                                          {"type": "text", "text": "Done."}]},
    ]
    out = to_openai_messages("sys", hist)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "assistant"]
    parts = out[1]["content"]
    assert parts[0]["text"].startswith('<document title="notes.md">') and "Rev +12%" in parts[0]["text"]
    assert "scan.pdf" in parts[1]["text"]  # PDF becomes extracted text (blank page -> note)
    assert parts[2]["type"] == "image_url" and parts[2]["image_url"]["url"].startswith("data:image/png;base64,")
    assert out[2]["tool_calls"][0]["function"] == {"name": "fundamentals", "arguments": '{"ticker": "EQT"}'}
    assert out[3] == {"role": "tool", "tool_call_id": "toolu_1", "content": "pe 12"}
    assert out[4] == {"role": "assistant", "content": "Done."}
