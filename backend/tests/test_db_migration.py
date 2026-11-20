"""Older databases: columns added later get their defaults filled in, so saving old rows works."""
import json

from sqlalchemy import create_engine, text

from app import db as app_db


def test_columns_added_to_an_old_table_get_defaults(monkeypatch, tmp_path):
    old = create_engine(f"sqlite:///{tmp_path}/old.db")
    with old.begin() as conn:  # a scanner table from before source/params/universe_index existed
        conn.execute(text('CREATE TABLE screener (id INTEGER PRIMARY KEY, slug VARCHAR, name VARCHAR, '
                          'description VARCHAR, universe JSON, rules JSON, sort_by VARCHAR, "limit" INTEGER, '
                          'follow_up_prompt VARCHAR, enabled BOOLEAN)'))
        conn.execute(text('INSERT INTO screener (id, slug, name, description, universe, rules, "limit", '
                          "follow_up_prompt, enabled) VALUES (1, 'oversold', 'Oversold', '', '[\"AAPL\"]', '{}', 25, '', 1)"))
    monkeypatch.setattr(app_db, "engine", old)
    app_db.add_missing_columns()
    with old.connect() as conn:
        assert conn.execute(text("SELECT source, params FROM screener WHERE id = 1")).one() == (None, None)
    app_db.fill_missing_defaults()
    with old.connect() as conn:
        source, params, index = conn.execute(text("SELECT source, params, universe_index FROM screener WHERE id = 1")).one()
    assert source == "rules" and json.loads(params) == {} and index is None

    from sqlmodel import Session

    from app.models import Screener
    with Session(old) as s:
        Screener.model_validate(s.get(Screener, 1).model_dump())  # what saving does: no longer fails
