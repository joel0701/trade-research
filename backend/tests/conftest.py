import os
import tempfile

# Point the app at a throwaway database before any app module is imported.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
