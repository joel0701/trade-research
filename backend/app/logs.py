"""Log files in data/logs (LOG_DIR):

- backend_<YYYY-MM-DD_HHMMSS>.log: everything the backend logs, with tracebacks.
- frontend_<YYYY-MM-DD_HHMMSS>.log: errors reported by the browser.

The timestamp is when the file was created. A new file starts at every app start, when the
current one reaches LOG_MAX_MB (default 5), and at midnight. Old files are deleted after
LOG_KEEP_DAYS (default 14), and the oldest go first once one kind's files exceed
LOG_MAX_TOTAL_MB (default 200). backend-latest.log / frontend-latest.log always point at the
current file (for `tail -F`).
"""
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.config import LOG_DIR

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


class TimestampedFileHandler(logging.Handler):
    """Writes to <prefix>_<created>.log, starting a new file on size, at midnight, and per process."""

    def __init__(self, directory: Path, prefix: str, max_bytes: int | None = None, keep_days: float | None = None,
                 max_total_bytes: int | None = None, now=datetime.now):
        super().__init__()
        self.dir, self.prefix, self._now = Path(directory), prefix, now
        self.max_bytes = int(max_bytes if max_bytes is not None else _env_num("LOG_MAX_MB", 5) * 1_000_000)
        self.keep_days = keep_days if keep_days is not None else _env_num("LOG_KEEP_DAYS", 14)
        self.max_total = int(max_total_bytes if max_total_bytes is not None
                             else _env_num("LOG_MAX_TOTAL_MB", 200) * 1_000_000)
        self.stream = None
        self.path: Path | None = None
        self.day = None
        self._open_new()

    # -- files -------------------------------------------------------------
    def _open_new(self) -> None:
        if self.stream:
            self.stream.close()
        self.dir.mkdir(parents=True, exist_ok=True)
        now = self._now()
        stamp = now.strftime("%Y-%m-%d_%H%M%S")
        path, n = self.dir / f"{self.prefix}_{stamp}.log", 2
        while path.exists():  # two files in the same second (e.g. size rollover under heavy logging)
            path, n = self.dir / f"{self.prefix}_{stamp}-{n}.log", n + 1
        self.path, self.day = path, now.date()
        self.stream = open(path, "a", encoding="utf-8")
        self._point_latest()
        self._cleanup()

    def _point_latest(self) -> None:
        link = self.dir / f"{self.prefix}-latest.log"
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(self.path.name)
        except OSError:
            pass  # symlinks unavailable (e.g. some Windows setups): the timestamped file still works

    def files(self) -> list[Path]:
        """This kind's log files, oldest first (the timestamp in the name sorts by time)."""
        return sorted(p for p in self.dir.glob(f"{self.prefix}_*.log") if not p.is_symlink())

    def _cleanup(self) -> None:
        cutoff = self._now() - timedelta(days=self.keep_days)
        files = [p for p in self.files() if p != self.path]
        for p in list(files):
            try:
                if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
                    p.unlink()
                    files.remove(p)
            except OSError:
                pass
        total = sum(p.stat().st_size for p in files if p.exists()) + (self.path.stat().st_size if self.path else 0)
        for p in files:  # oldest first
            if total <= self.max_total:
                break
            try:
                size = p.stat().st_size
                p.unlink()
                total -= size
            except OSError:
                pass

    # -- logging -----------------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record) + "\n"
            if self.stream is None:
                return
            now = self._now()
            size = self.stream.tell()
            if now.date() != self.day or (size > 0 and size + len(msg.encode("utf-8")) > self.max_bytes):
                self._open_new()
            self.stream.write(msg)
            self.stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self.lock:
            if self.stream:
                self.stream.close()
                self.stream = None
        super().close()


def _adopt_old_file(prefix: str) -> None:
    """An earlier version wrote plain backend.log / frontend.log: give it a timestamped name so the
    normal cleanup handles it."""
    old = LOG_DIR / f"{prefix}.log"
    if old.is_file() and not old.is_symlink():
        stamp = datetime.fromtimestamp(old.stat().st_mtime).strftime("%Y-%m-%d_%H%M%S")
        try:
            old.rename(LOG_DIR / f"{prefix}_{stamp}-old.log")
        except OSError:
            pass


def _handler(prefix: str) -> TimestampedFileHandler:
    _adopt_old_file(prefix)
    h = TimestampedFileHandler(LOG_DIR, prefix)
    h.setFormatter(logging.Formatter(FORMAT))
    return h


def setup_logging() -> None:
    """Console plus the backend log file. Safe to call more than once in a process."""
    root = logging.getLogger()
    if any(getattr(h, "_trade_research", False) for h in root.handlers):
        return
    if not any(type(h) is logging.StreamHandler for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(console)
    root.setLevel(logging.INFO)
    backend = _handler("backend")
    backend._trade_research = True  # type: ignore[attr-defined]
    root.addHandler(backend)
    # Uvicorn's own loggers don't propagate to the root logger: give them the file too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        if not lg.propagate and backend not in lg.handlers:
            lg.addHandler(backend)
    # Browser errors go to their own file (and not the backend log).
    fe = logging.getLogger("frontend")
    fe.propagate = False
    fe.setLevel(logging.INFO)
    fe.addHandler(_handler("frontend"))
