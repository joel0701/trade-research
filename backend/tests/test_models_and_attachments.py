"""Per-session Lead model, skill default models, and document attachments."""
import base64
import json

from fastapi.testclient import TestClient

from app.agents import loop
from app.main import app
from tests.test_orchestrator import FakeClient


def model_id(c, slug):
    return next(t["id"] for t in c.get("/api/tools").json() if t["slug"] == slug)


def ask(c, sid, body):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json=body) as r:
        assert r.status_code == 200, r.read()
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_session_lead_model_is_used(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        sonnet = model_id(c, "sonnet")
        sid = c.post("/api/sessions", json={"lead_model_tool_id": sonnet}).json()["id"]
        ask(c, sid, {"text": "Quick question"})
        lead_calls = [k for k in fake.calls if "Your team" in k["system"]]
        assert lead_calls and all(k["model"] == "claude-sonnet-5-5" for k in lead_calls)

        # Switch mid-session; null goes back to the Lead role's default (Opus).
        fable = model_id(c, "fable")
        assert c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": fable}).json()["lead_model_tool_id"] == fable
        assert c.get(f"/api/sessions/{sid}").json()["title"] == "Quick question"  # title untouched
        assert c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": None}).json()["lead_model_tool_id"] is None
        fake.calls.clear()
        ask(c, sid, {"text": "Again"})
        assert next(k for k in fake.calls if "Your team" in k["system"])["model"] == "claude-opus-5-5"

        # Only models are accepted.
        web = model_id(c, "web_search")
        assert c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": web}).status_code == 422


def test_skill_model_default_and_override():
    with TestClient(app) as c:
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        haiku, fable = model_id(c, "haiku"), model_id(c, "fable")
        c.put(f"/api/skills/{skill['id']}", json={"lead_model_tool_id": haiku})
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "AAPL"}}).json()
        assert c.get(f"/api/sessions/{r['session_id']}").json()["lead_model_tool_id"] == haiku
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "AAPL"}, "lead_model_tool_id": fable}).json()
        assert c.get(f"/api/sessions/{r['session_id']}").json()["lead_model_tool_id"] == fable


def test_attachments_reach_claude_and_show_in_chat(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        notes = c.post(f"/api/sessions/{sid}/attachments",
                       files={"file": ("notes.md", b"# Q3\nRevenue up 12%", "text/markdown")}).json()
        chart = c.post(f"/api/sessions/{sid}/attachments", files={"file": ("chart.png", png, "image/png")}).json()
        assert notes["kind"] == "text" and chart["kind"] == "image"

        bad = c.post(f"/api/sessions/{sid}/attachments", files={"file": ("deck.pptx", b"x", "application/octet-stream")})
        assert bad.status_code == 415 and "PDF" in bad.json()["detail"]

        spare = c.post(f"/api/sessions/{sid}/attachments", files={"file": ("spare.txt", b"x", "text/plain")}).json()
        assert c.delete(f"/api/sessions/{sid}/attachments/{spare['id']}").json() == {"ok": True}

        ask(c, sid, {"text": "Summarise these", "attachment_ids": [notes["id"], chart["id"]]})
        first_lead = next(k for k in fake.calls if "Your team" in k["system"])
        types = [b["type"] for b in first_lead["messages"][0]["content"]]
        assert types == ["document", "image", "text"]  # files before the question
        doc = first_lead["messages"][0]["content"][0]
        assert doc["title"] == "notes.md" and "Revenue up 12%" in doc["source"]["data"]

        chat = c.get(f"/api/sessions/{sid}").json()
        assert [a["filename"] for a in chat["messages"][0]["attachments"]] == ["notes.md", "chart.png"]
        # Sent files are part of the conversation: can't be removed or reused.
        assert c.delete(f"/api/sessions/{sid}/attachments/{notes['id']}").status_code == 409
        assert c.get(f"/api/sessions/{sid}/attachments/{notes['id']}").content == b"# Q3\nRevenue up 12%"

        # A follow-up still includes the document in the history sent to Claude.
        fake.calls.clear()
        ask(c, sid, {"text": "And the margin?"})
        lead = next(k for k in fake.calls if "Your team" in k["system"])
        assert lead["messages"][0]["content"][0]["type"] == "document"

        # File-only message gets a default prompt.
        other = c.post(f"/api/sessions/{sid}/attachments", files={"file": ("q4.csv", b"a,b\n1,2", "text/csv")}).json()
        ask(c, sid, {"attachment_ids": [other["id"]]})
        assert c.post(f"/api/sessions/{sid}/messages", json={"text": "  "}).status_code == 422

        assert c.delete(f"/api/sessions/{sid}").json() == {"ok": True}
