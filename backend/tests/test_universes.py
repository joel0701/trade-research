"""Stock lists (indexes) for scanners, skills and questions, and batched, cached Yahoo downloads."""
import asyncio
import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine as db_engine
from app.main import app
from app.models import StockList, now
from app.screeners import engine, universes
from app.tools import python_tools

PAGE = """<html><body>
<table><tr><th>Symbol</th></tr><tr><td>X</td></tr></table>
<table><thead><tr><th>Symbol</th><th>Security</th></tr></thead><tbody>
<tr><td>MMM</td><td>3M</td></tr><tr><td>BRK.B</td><td>Berkshire</td></tr><tr><td>AAPL</td><td>Apple</td></tr>
<tr><td>MSFT</td><td>Microsoft</td></tr><tr><td>NVDA</td><td>Nvidia</td></tr><tr><td>AMZN</td><td>Amazon</td></tr>
</tbody></table></body></html>"""


def test_parse_constituents_picks_the_biggest_table_with_the_column():
    assert universes.parse_constituents(PAGE, "Symbol") == ["MMM", "BRK-B", "AAPL", "MSFT", "NVDA", "AMZN"]


def reset(slug):
    with Session(db_engine) as db:
        sl = db.exec(select(StockList).where(StockList.slug == slug)).one()
        sl.tickers, sl.refreshed_at, sl.last_error, sl.refresh = [], None, None, "weekly"
        db.add(sl)
        db.commit()


def test_lists_refresh_only_when_used_and_due(monkeypatch):
    calls = []

    def fetch(sl):
        calls.append(sl.slug)
        return ["AAA", "BBB"]
    monkeypatch.setattr(universes, "fetch_list", fetch)
    with TestClient(app) as c:
        lists = {x["slug"]: x for x in c.get("/api/stock-lists").json()}
        assert {"sp500", "nasdaq100", "dow30"} <= set(lists) and lists["dow30"]["refresh"] == "monthly"
        reset("dow30")
        assert calls == []  # nothing is fetched until something uses the list
        with Session(db_engine) as db:
            sl, note = universes.get_list(db, "Dow 30")
            assert sl.tickers == ["AAA", "BBB"] and note is None and calls == ["dow30"]
            universes.get_list(db, "dow30")  # fresh: not fetched again
            assert calls == ["dow30"]
            sl.refreshed_at = now() - timedelta(days=31)  # a month old: due (monthly)
            db.add(sl)
            db.commit()

            def broken(sl):
                raise OSError("offline")
            monkeypatch.setattr(universes, "fetch_list", broken)
            sl, note = universes.get_list(db, "DOW-30")
            assert sl.tickers == ["AAA", "BBB"] and note.startswith("Couldn't refresh the Dow 30 list; using the one saved on")
            assert "offline" in sl.last_error
        reset("dow30")


def test_manual_list_and_name_matching():
    with TestClient(app) as c:
        r = c.post("/api/stock-lists", json={"slug": "my_ai", "name": "My AI names", "source": "manual",
                                             "refresh": "manual", "tickers": ["NVDA", "AMD", "AVGO"]}).json()
        with Session(db_engine) as db:
            sl, note = universes.get_list(db, "my ai names")
            assert sl.tickers == ["NVDA", "AMD", "AVGO"] and note is None
            assert universes.find_list(db, "S&P500").slug == "sp500"
        c.delete(f"/api/stock-lists/{r['id']}")


def test_agent_tool_lists_and_returns_members(monkeypatch):
    monkeypatch.setattr(universes, "fetch_list", lambda sl: ["AAA", "BBB", "CCC"])
    with TestClient(app) as c:
        reset("nasdaq100")
        overview = json.loads(asyncio.run(python_tools.stock_list()))
        assert any(x["name"] == "Nasdaq-100" for x in overview["lists"])
        out = json.loads(asyncio.run(python_tools.stock_list("nasdaq 100")))
        assert out["name"] == "Nasdaq-100" and out["tickers"] == ["AAA", "BBB", "CCC"] and out["as_of"]
        assert "No stock list called" in asyncio.run(python_tools.stock_list("Russell 2000"))
        team = {r["slug"]: r for r in c.get("/api/team").json()}
        tool_id = next(t["id"] for t in c.get("/api/tools").json() if t["slug"] == "stock_list")
        assert tool_id in team["lead"]["tool_ids"]
        reset("nasdaq100")


def test_scanner_runs_on_a_stock_list_plus_extra_tickers(monkeypatch):
    members = [f"T{i:03d}" for i in range(503)]
    monkeypatch.setattr(universes, "fetch_list", lambda sl: members)
    seen = {}

    def fake_fetch(tickers):
        seen["tickers"] = tickers
        return {t: {"close": 10, "sma_200": 5, "rsi_14": 30 + (i % 20)} for i, t in enumerate(tickers)}
    monkeypatch.setattr(engine, "fetch_snapshots", fake_fetch)
    with TestClient(app) as c:
        reset("sp500")
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "oversold_uptrend")
        assert any(u["key"] == "sp500" for u in c.get("/api/universes").json())
        c.put(f"/api/screeners/{sc['id']}", json={"universe_index": "sp500", "universe": ["T001", "COIN"]})
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        assert len(seen["tickers"]) == 504 and seen["tickers"][-1] == "COIN"
        ins = c.get(f"/api/sessions/{r['session_id']}").json()["messages"][0]["text"]
        assert "- Universe: **S&P 500** (503 tickers, as of " in ins and "- Extra tickers: **COIN**" in ins
        assert "T250" not in ins
        c.post(f"/api/screeners/{sc['id']}/run", json={"universe_index": "", "universe": ["AAPL"]})
        assert seen["tickers"] == ["AAPL"]
        c.put(f"/api/screeners/{sc['id']}", json={"universe_index": None, "universe": sc["universe"]})
        reset("sp500")


def test_downloads_are_batched_and_cached(monkeypatch):
    engine._snap_cache.clear()
    batches = []

    def download(tickers):
        batches.append(len(tickers))
        return {t: {"close": 1} for t in tickers}
    monkeypatch.setattr(engine, "_download", download)
    tickers = [f"S{i}" for i in range(250)]
    assert len(engine.fetch_snapshots(tickers)) == 250 and batches == [100, 100, 50]
    assert len(engine.fetch_snapshots(tickers + ["NEW"])) == 251 and batches[-1] == 1
    engine._snap_cache.clear()
