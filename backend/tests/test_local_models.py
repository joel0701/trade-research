"""Local model servers: available while running, discovered models can be added, no key needed."""
import httpx
from fastapi.testclient import TestClient

from app.llm import providers
from app.main import app


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


def fake_get(running: dict[str, list[str]]):
    def get(url, timeout=None):
        for base, models in running.items():
            if url.startswith(base):
                return FakeResp({"data": [{"id": m} for m in models]})
        raise httpx.ConnectError("refused")
    return get


def test_discover_and_add_local_models(monkeypatch):
    providers._reach_cache.clear()
    monkeypatch.setattr(httpx, "get", fake_get({"http://localhost:11434/v1": ["llama3.1:8b", "qwen2.5:14b", "nomic-embed-text"],
                                                "http://localhost:8080/v1": ["mlx-community/Qwen2.5-7B-Instruct-4bit"]}))
    with TestClient(app) as c:
        servers = {s["provider"]: s for s in c.get("/api/local-models").json()}
        assert servers["ollama"]["running"] and [m["model_id"] for m in servers["ollama"]["models"]] == ["llama3.1:8b", "qwen2.5:14b"]
        assert servers["mlx"]["running"] and servers["lmstudio"]["running"] is False
        assert servers["local"]["base_url"] is None and not servers["local"]["running"]  # needs LOCAL_LLM_BASE_URL

        tool = c.post("/api/local-models", json={"provider": "ollama", "model_id": "llama3.1:8b"}).json()
        assert tool["kind"] == "model" and tool["config"]["provider"] == "ollama" and tool["config"]["price_input"] == 0
        assert c.post("/api/local-models", json={"provider": "ollama", "model_id": "llama3.1:8b"}).json()["id"] == tool["id"]
        model = next(m for m in c.get("/api/models").json() if m["id"] == tool["id"])
        assert model["available"] and model["local"]
        # It can be picked as a session's Lead model (no key needed).
        sid = c.post("/api/sessions", json={}).json()["id"]
        assert c.patch(f"/api/sessions/{sid}", json={"lead_model_tool_id": tool["id"]}).status_code == 200

        # Server stopped: the model is unavailable.
        providers._reach_cache.clear()
        monkeypatch.setattr(httpx, "get", fake_get({}))
        assert not next(m for m in c.get("/api/models").json() if m["id"] == tool["id"])["available"]
        c.delete(f"/api/tools/{tool['id']}")
    providers._reach_cache.clear()


def test_model_without_tool_calling_still_answers(monkeypatch):
    """Ollama rejects tools for some models: the Lead answers without the team instead of failing."""
    from types import SimpleNamespace as NS
    from app.llm import openai_loop
    from tests.test_vendors import chunk

    calls = []

    class Completions:
        async def create(self, **kw):
            calls.append(kw)
            if "tools" in kw:
                raise RuntimeError("Error code: 400 - registry.ollama.ai/library/gemma:2b does not support tools")

            async def stream():
                for c in [chunk(content="From general knowledge: "), chunk(content="NVDA is volatile."), chunk(finish="stop")]:
                    yield c
            return stream()

    monkeypatch.setattr(openai_loop, "openai_client", lambda provider: NS(chat=NS(completions=Completions())))
    providers._reach_cache.clear()
    monkeypatch.setattr(httpx, "get", fake_get({"http://localhost:11434/v1": ["gemma:2b"]}))
    with TestClient(app) as c:
        tool = c.post("/api/local-models", json={"provider": "ollama", "model_id": "gemma:2b"}).json()
        sid = c.post("/api/sessions", json={"lead_model_tool_id": tool["id"]}).json()["id"]
        with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": "How is NVDA?"}) as r:
            events = [line for line in r.iter_lines() if line.startswith("data: ")]
        assert '"done"' in events[-1]
        assert "tools" in calls[0] and "tools" not in calls[1] and "can't call tools" in calls[1]["messages"][0]["content"]
        last = c.get(f"/api/sessions/{sid}").json()["messages"][-1]
        assert last["text"] == "From general knowledge: NVDA is volatile." and last["model"] == "gemma:2b"
        c.delete(f"/api/tools/{tool['id']}")
    providers._reach_cache.clear()
