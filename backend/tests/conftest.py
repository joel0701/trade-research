import os
import tempfile

# Point the app at a throwaway database before any app module is imported.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
# Only Anthropic is "configured" in tests unless a test sets another vendor's key.
for _k in ("OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "LLAMA_API_KEY", "XAI_API_KEY",
           "MISTRAL_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(_k, None)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_preflight_prompt(request, monkeypatch):
    """Skip the unavailable-sources prompt except in tests that exercise it."""
    if "preflight" in request.node.name:
        return
    from app.api import sessions

    monkeypatch.setattr(sessions, "unavailable_report", lambda db: None)
