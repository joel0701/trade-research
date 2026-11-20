"""App settings, read from environment variables (and a .env file for local dev)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'trade_research.db'}")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
# Hard cap on agent-loop iterations per role run, regardless of role config.
MAX_LOOP_ITERATIONS = int(os.getenv("MAX_LOOP_ITERATIONS", "25"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", str(ROOT / "data" / "cache")))
LOG_DIR = Path(os.getenv("LOG_DIR", str(ROOT / "data" / "logs")))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(ROOT / "data" / "uploads")))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
