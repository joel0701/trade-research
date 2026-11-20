"""The profile's Test mode toggle: takes effect at once, survives restarts, .env still forces it on."""
from fastapi.testclient import TestClient

from app.agents import loop
from app.llm import mock_loop
from app.main import app
from tests.test_orchestrator import FakeClient


def ask(c, sid, text):
    import json
    with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": text}) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_toggle_switches_mock_mode_without_restart(monkeypatch):
    monkeypatch.delenv("MOCK_LLM", raising=False)
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    try:
        with TestClient(app) as c:
            assert c.get("/api/health").json()["mock_llm"] is False
            p = c.put("/api/profile", json={"mock_llm": True}).json()
            assert p["mock_llm"] is True and p["mock_llm_env"] is False
            assert c.get("/api/health").json()["mock_llm"] is True
            sid = c.post("/api/sessions", json={}).json()["id"]
            ask(c, sid, "How is NVDA doing?")
            assert fake.calls == []  # no real model was called
            assert "Test mode response" in c.get(f"/api/sessions/{sid}").json()["messages"][-1]["text"]
        mock_loop.set_mock(False)  # simulate a fresh process
        with TestClient(app) as c:  # restart: the saved toggle is applied
            assert c.get("/api/health").json()["mock_llm"] is True
            c.put("/api/profile", json={"mock_llm": False})
            assert c.get("/api/health").json()["mock_llm"] is False
            monkeypatch.setenv("MOCK_LLM", "1")  # .env forces it on regardless
            h = c.get("/api/health").json()
            assert h["mock_llm"] is True and h["mock_llm_env"] is True
    finally:
        mock_loop.set_mock(False)
