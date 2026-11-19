"""Log files: data/logs/backend.log (everything the backend logs, with tracebacks) and
data/logs/frontend.log (errors reported by the browser). Both rotate at 5 MB, keeping 3 old files."""
import logging
from logging.handlers import RotatingFileHandler

from app.config import LOG_DIR

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _file(name: str) -> RotatingFileHandler:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(LOG_DIR / name, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter(FORMAT))
    return h


def setup_logging() -> None:
    """Console plus backend.log. Safe to call more than once (e.g. on --reload)."""
    root = logging.getLogger()
    if any(getattr(h, "_trade_research", False) for h in root.handlers):
        return
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(console)
    root.setLevel(logging.INFO)
    backend = _file("backend.log")
    backend._trade_research = True  # type: ignore[attr-defined]
    root.addHandler(backend)
    # Uvicorn's own loggers don't propagate to the root logger: give them the file too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        if not lg.propagate:
            lg.addHandler(backend)
    # Browser errors go to their own file (and not the backend log).
    fe = logging.getLogger("frontend")
    fe.propagate = False
    fe.setLevel(logging.INFO)
    fe.addHandler(_file("frontend.log"))
