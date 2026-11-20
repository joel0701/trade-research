from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_URL

if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    add_missing_columns()
    fill_missing_defaults()


def add_missing_columns() -> None:
    """Add columns introduced after a database was created (create_all only makes new tables).

    Good enough for additive changes to a local SQLite database. Use Alembic for anything
    more involved, or before moving to a shared Postgres database.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    coltype = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'))


def _table_models() -> list[type[SQLModel]]:
    seen, out, todo = set(), [], list(SQLModel.__subclasses__())
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.add(cls)
        todo.extend(cls.__subclasses__())
        if getattr(cls, "__table__", None) is not None:
            out.append(cls)
    return out


def fill_missing_defaults() -> None:
    """Give rows the default value of fields that don't allow empty values but are empty, e.g. a
    column added after the rows were created (SQLite adds it as NULL). Runs at every startup."""
    import json

    from pydantic_core import PydanticUndefined
    from sqlalchemy import JSON

    insp = inspect(engine)
    with engine.begin() as conn:
        for cls in _table_models():
            table = cls.__table__
            if not insp.has_table(table.name):
                continue
            present = {c["name"] for c in insp.get_columns(table.name)}
            for name, field in cls.model_fields.items():
                if name not in present:
                    continue
                col = table.columns.get(name)
                if col is None or col.primary_key:
                    continue
                if field.default_factory is not None:
                    value = field.default_factory()
                elif field.default is not PydanticUndefined:
                    value = field.default
                else:
                    continue
                if value is None:
                    continue  # the field allows empty values
                if isinstance(col.type, JSON):  # JSON columns can also hold a JSON null
                    conn.execute(text(f'UPDATE "{table.name}" SET "{name}" = :v WHERE "{name}" IS NULL OR "{name}" = \'null\''),
                                 {"v": json.dumps(value)})
                else:
                    conn.execute(table.update().where(col.is_(None)).values({name: value}))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
