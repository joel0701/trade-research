"""Log files: backend.log gets the backend's logs, frontend.log gets browser-reported errors."""
import logging

from fastapi.testclient import TestClient

from app.config import LOG_DIR
from app.main import app


def flush():
    for lg in (logging.getLogger(), logging.getLogger("frontend")):
        for h in lg.handlers:
            h.flush()


def test_backend_and_frontend_log_files():
    with TestClient(app) as c:
        logging.getLogger("app.test").error("backend log check 123")
        r = c.post("/api/client-log", json={"message": "TypeError: x is undefined", "detail": "at Research.tsx:42",
                                            "url": "http://localhost:5173/research/5"})
        assert r.status_code == 204
        flush()
        backend = (LOG_DIR / "backend.log").read_text()
        frontend = (LOG_DIR / "frontend.log").read_text()
        assert "backend log check 123" in backend and "TypeError" not in backend
        assert "ERROR frontend: TypeError: x is undefined | http://localhost:5173/research/5\nat Research.tsx:42" in frontend
