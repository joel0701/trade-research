"""Log files: timestamped names, a new file per start / size / day, and cleanup by age and total size."""
import logging
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.config import LOG_DIR
from app.logs import TimestampedFileHandler
from app.main import app


def latest(prefix: str) -> str:
    return (LOG_DIR / f"{prefix}-latest.log").read_text()


def test_backend_and_frontend_log_files():
    with TestClient(app) as c:
        logging.getLogger("app.test").error("backend log check 123")
        r = c.post("/api/client-log", json={"message": "TypeError: x is undefined", "detail": "at Research.tsx:42",
                                            "url": "http://localhost:5173/research/5"})
        assert r.status_code == 204
        backend, frontend = latest("backend"), latest("frontend")
        assert "backend log check 123" in backend and "TypeError" not in backend
        assert "ERROR frontend: TypeError: x is undefined | http://localhost:5173/research/5\nat Research.tsx:42" in frontend
        names = [p.name for p in LOG_DIR.glob("backend_*.log")]
        assert names and all(len(n) >= len("backend_2026-01-01_000000.log") for n in names)


def make(tmp_path, clock, **kw):
    h = TimestampedFileHandler(tmp_path, "backend", now=lambda: clock[0], **kw)
    h.setFormatter(logging.Formatter("%(message)s"))
    return h


def log(h, text):
    h.emit(logging.LogRecord("t", logging.INFO, __file__, 1, text, None, None))


def test_new_file_per_start_size_and_day(tmp_path):
    clock = [datetime(2026, 11, 19, 9, 30, 0)]
    h = make(tmp_path, clock, max_bytes=100, keep_days=14, max_total_bytes=10**9)
    assert h.path.name == "backend_2026-11-19_093000.log"
    log(h, "a" * 60)
    clock[0] += timedelta(seconds=5)
    log(h, "b" * 60)  # would pass 100 bytes: new file
    assert h.path.name == "backend_2026-11-19_093005.log" and len(h.files()) == 2
    clock[0] = datetime(2026, 11, 20, 0, 0, 1)
    log(h, "after midnight")  # new day: new file
    assert h.path.name == "backend_2026-11-20_000001.log"
    assert (tmp_path / "backend-latest.log").read_text().strip() == "after midnight"
    h.close()
    h2 = make(tmp_path, clock, max_bytes=100)  # an app start in the same second: still a new file
    assert h2.path.name == "backend_2026-11-20_000001-2.log" and len(h2.files()) == 4
    h2.close()


def test_cleanup_by_age_and_total_size(tmp_path):
    now = datetime(2026, 11, 19, 12, 0, 0)
    old = tmp_path / "backend_2026-10-01_080000.log"
    old.write_text("old")
    stamp = (now - timedelta(days=30)).timestamp()
    os.utime(old, (stamp, stamp))
    for i in range(5):  # recent files, 1000 bytes each
        f = tmp_path / f"backend_2026-11-1{i}_080000.log"
        f.write_text("x" * 1000)
    other = tmp_path / "frontend_2026-10-01_080000.log"  # a different kind is left alone
    other.write_text("keep")
    os.utime(other, (stamp, stamp))
    h = make(tmp_path, [now], max_bytes=10**6, keep_days=14, max_total_bytes=2500)
    left = [p.name for p in h.files()]
    assert "backend_2026-10-01_080000.log" not in left          # too old
    assert left[:-1] == ["backend_2026-11-13_080000.log", "backend_2026-11-14_080000.log"]  # oldest went first
    assert left[-1] == h.path.name and other.exists()
    h.close()
