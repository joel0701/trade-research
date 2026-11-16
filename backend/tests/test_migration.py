"""An older database missing newer columns is upgraded in place on startup."""
from sqlalchemy import create_engine, inspect, text

from app import db as dbmod


def test_add_missing_columns(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:  # a message table from before run_id existed
        conn.execute(text("CREATE TABLE message (id INTEGER PRIMARY KEY, session_id INTEGER, role VARCHAR, "
                          "content JSON, visible BOOLEAN, model VARCHAR, usage JSON, created_at DATETIME)"))
        conn.execute(text("INSERT INTO message (session_id, role, content, visible) VALUES (1, 'user', '[]', 1)"))
    monkeypatch.setattr(dbmod, "engine", engine)
    dbmod.init_db()
    cols = {c["name"] for c in inspect(engine).get_columns("message")}
    assert "run_id" in cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM message")).scalar() == 1  # data kept
