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


def no_auto_find(monkeypatch):
    def nothing(symbol, min_count=0):
        raise universes.UniverseError("not in tests")
    monkeypatch.setattr(universes, "_run_in_thread", nothing)


def reset(slug):
    with Session(db_engine) as db:
        sl = db.exec(select(StockList).where(StockList.slug == slug)).one()
        sl.tickers, sl.refreshed_at, sl.last_error, sl.refresh = [], None, None, "weekly"
        sl.url, sl.source_checked_at, sl.last_source = "", None, ""
        db.add(sl)
        db.commit()


def test_lists_refresh_only_when_used_and_due(monkeypatch):
    no_auto_find(monkeypatch)
    calls = []

    def fetch(sl):
        calls.append(sl.slug)
        return ["AAA", "BBB"], "backup"
    monkeypatch.setattr(universes, "fetch_list", fetch)
    with TestClient(app) as c:
        lists = {x["slug"]: x for x in c.get("/api/stock-lists").json()}
        assert {"sp500", "nasdaq100", "dow30", "schd"} <= set(lists) and lists["dow30"]["refresh"] == "monthly"
        assert lists["sp500"]["name"] == "S&P 500 (SPY)" and lists["sp500"]["fallback_url"].startswith("https://en.wikipedia.org")
        reset("dow30")
        assert calls == []  # nothing is fetched until something uses the list
        with Session(db_engine) as db:
            sl, note = universes.get_list(db, "Dow 30")
            assert sl.tickers == ["AAA", "BBB"] and note is None and calls == ["dow30"] and sl.last_source == "backup"
            universes.get_list(db, "dow30")  # fresh: not fetched again
            assert calls == ["dow30"]
            sl.refreshed_at = now() - timedelta(days=31)  # a month old: due (monthly)
            db.add(sl)
            db.commit()

            def broken(sl):
                raise OSError("offline")
            monkeypatch.setattr(universes, "fetch_list", broken)
            sl, note = universes.get_list(db, "DIA")  # by ETF symbol
            assert sl.tickers == ["AAA", "BBB"] and note.startswith("Couldn't refresh the Dow 30 (DIA) list; using the one saved on")
            assert "offline" in sl.last_error and "Find source" in sl.last_error
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
    no_auto_find(monkeypatch)
    monkeypatch.setattr(universes, "fetch_list", lambda sl: (["AAA", "BBB", "CCC"], "main"))
    with TestClient(app) as c:
        reset("nasdaq100")
        overview = json.loads(asyncio.run(python_tools.stock_list()))
        assert any(x["name"] == "Nasdaq-100 (QQQ)" for x in overview["lists"])
        out = json.loads(asyncio.run(python_tools.stock_list("nasdaq 100")))
        assert out["name"] == "Nasdaq-100 (QQQ)" and json.loads(asyncio.run(python_tools.stock_list("QQQ")))["count"] == 3 and out["tickers"] == ["AAA", "BBB", "CCC"] and out["as_of"]
        assert "No stock list called" in asyncio.run(python_tools.stock_list("Russell 2000"))
        team = {r["slug"]: r for r in c.get("/api/team").json()}
        tool_id = next(t["id"] for t in c.get("/api/tools").json() if t["slug"] == "stock_list")
        assert tool_id in team["lead"]["tool_ids"]
        reset("nasdaq100")


def test_scanner_runs_on_a_stock_list_plus_extra_tickers(monkeypatch):
    no_auto_find(monkeypatch)
    members = [f"T{i:03d}" for i in range(503)]
    monkeypatch.setattr(universes, "fetch_list", lambda sl: (members, "main"))
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
        assert "- Universe: **S&P 500 (SPY)** (503 tickers, as of " in ins and "- Extra tickers: **COIN**" in ins
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


def test_csv_holdings_file_with_preamble():
    text = 'Fund Holdings as of Nov 19, 2026\n"Ticker","Name","Weight"\n"NVDA","Nvidia","8.1"\n"AAPL","Apple","7.0"\n' \
           '"MSFT","Microsoft","6.5"\n"AVGO","Broadcom","5.0"\n"BRK.B","Berkshire","1.0"\n"-","Cash","0.1"\n'
    assert universes.parse_csv(text, "ticker") == ["NVDA", "AAPL", "MSFT", "AVGO", "BRK-B"]


def test_find_source_etf_main_and_wikipedia_backup(monkeypatch):
    from app.screeners import discover

    class R:
        def __init__(self, text, ctype="text/html"):
            self.text, self.headers = text, {"content-type": ctype}
            self.content, self.status_code = text.encode(), 200

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads(self.text)

    big = "<table><tr><th>Ticker</th><th>Company</th></tr>" + "".join(
        f"<tr><td>T{i}</td><td>Co {i}</td></tr>" for i in range(400)) + "</table>"
    csv = "Fund holdings\nTicker,Name\n" + "".join(f"E{i},Co\n" for i in range(30))

    def fake_get(url, params=None, **kw):
        if url == discover.WIKI_API:
            q = params["srsearch"]
            return R(json.dumps({"query": {"search": [{"title": "List of S&P 400 companies"}] if "400" in q else []}}))
        if "S&P_400" in url:
            return R(big)
        if url.endswith("holdings.csv"):
            return R(csv, "text/csv")
        raise discover.httpx.ConnectError("nope")
    monkeypatch.setattr(discover.httpx, "get", fake_get)
    monkeypatch.setattr(universes.httpx, "get", fake_get)

    async def model(db, query):
        return [{"url": f"https://issuer.test/{query.lower()}/holdings.csv", "column": "Ticker"}], "Claude Haiku 5.5"
    monkeypatch.setattr(discover, "model_candidates", model)
    with TestClient(app) as c:
        r = c.post("/api/stock-lists", json={"slug": "sp400", "name": "S&P 400 (MDY)", "symbol": "MDY",
                                             "source": "web", "refresh": "manual"}).json()
        out = c.post(f"/api/stock-lists/{r['id']}/find-source").json()
        assert out["url"].endswith("mdy/holdings.csv") and out["last_source"] == "main" and len(out["tickers"]) == 30
        assert "S&P_400" in out["fallback_url"] and out["fallback_column"] == "Ticker"
        assert "ETF holdings found by web search (Claude Haiku 5.5)" in out["source_note"] and out["refresh"] == "weekly"
        # The main source breaks: the next refresh uses the backup and says so.
        with Session(db_engine) as db:
            sl = db.get(StockList, r["id"])
            sl.url = "https://issuer.test/moved.csv"
            db.add(sl)
            db.commit()
            sl = universes.refresh(db, sl)
            assert sl.last_source == "backup" and len(sl.tickers) == 400 and "backup was used" in sl.last_error
        c.delete(f"/api/stock-lists/{r['id']}")


def test_auto_find_looks_for_the_etf_once(monkeypatch):
    calls = []

    def finder(symbol, min_count=0):
        calls.append(symbol)
        return {"url": "https://issuer.test/dia.xlsx", "column": "Ticker", "tickers": [], "found_by": "web search (test)"}
    monkeypatch.setattr(universes, "_run_in_thread", finder)
    monkeypatch.setattr(universes, "fetch_list", lambda sl: (["AAA"] * 1 + ["BBB"], "main" if sl.url else "backup"))
    with TestClient(app) as c:
        reset("dow30")
        with Session(db_engine) as db:
            sl, _ = universes.get_list(db, "dow 30")
            assert calls == ["DIA"] and sl.url == "https://issuer.test/dia.xlsx" and sl.last_source == "main"
            sl.url, sl.refreshed_at = "", None  # lost again: no new search within a month
            db.add(sl)
            db.commit()
            universes.get_list(db, "dow 30")
            assert calls == ["DIA"]
        reset("dow30")


def test_excel_holdings_file():
    import io
    import pandas as pd
    buf = io.BytesIO()
    rows = [["Fund Name:", "SPDR Dow Jones Industrial Average ETF"], ["As of", "19-Nov-2026"], [None, None],
            ["Name", "Ticker"]] + [[f"Company {i}", f"T{i}"] for i in range(30)]
    pd.DataFrame(rows).to_excel(buf, header=False, index=False)
    assert universes.parse_source(buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  "https://issuer.test/holdings-dia.xlsx", "Ticker")[:3] == ["T0", "T1", "T2"]


def test_unexpected_errors_come_back_as_readable_json(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(universes, "get_list", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "oversold_uptrend")
        r = c.post(f"/api/screeners/{sc['id']}/run", json={"universe_index": "sp500", "universe": []})
        assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
        assert "RuntimeError: disk on fire" in r.json()["detail"] and "backend-latest.log" in r.json()["detail"]


def test_incomplete_source_falls_back_to_the_backup(monkeypatch):
    """A main source with only part of the list (e.g. a top-holdings page) doesn't replace a full list."""
    def fetch_from(url, column):
        return [f"P{i}" for i in range(387)] if "partial" in url else [f"F{i}" for i in range(503)]
    monkeypatch.setattr(universes, "fetch_from", fetch_from)
    sl = StockList(slug="x", name="X", source="web", url="https://issuer.test/partial", min_count=480,
                   fallback_url="https://en.wikipedia.org/wiki/X")
    tickers, used = universes.fetch_list(sl)
    assert used == "backup" and len(tickers) == 503
    sl.fallback_url = ""
    try:
        universes.fetch_list(sl)
        raise AssertionError("expected an error")
    except universes.UniverseError as e:
        assert "only 387 tickers, expected at least 480" in str(e)
    with TestClient(app) as c:
        assert next(x for x in c.get("/api/stock-lists").json() if x["slug"] == "sp500")["min_count"] == 480


def test_find_source_explains_why_no_model_searched(monkeypatch):
    from app.llm import mock_loop
    from app.screeners import discover
    monkeypatch.setattr(discover, "find_wikipedia_source", lambda query, min_count=0: None)
    with TestClient(app) as c:
        r = c.post("/api/stock-lists", json={"slug": "qqqtest", "name": "QQQ test", "symbol": "QQQ"}).json()
        mock_loop.set_mock(True)
        try:
            out = c.post(f"/api/stock-lists/{r['id']}/find-source")
            assert out.status_code == 422 and "Test mode is on" in out.json()["detail"]
        finally:
            mock_loop.set_mock(False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = c.post(f"/api/stock-lists/{r['id']}/find-source")
        assert "ANTHROPIC_API_KEY isn't set" in out.json()["detail"]
        c.delete(f"/api/stock-lists/{r['id']}")


def test_find_source_reports_why_each_candidate_failed_and_reads_excel(monkeypatch):
    import io
    import pandas as pd
    from app.screeners import discover

    buf = io.BytesIO()
    pd.DataFrame([["Fund Name:", "SPDR"], ["Name", "Ticker"]] + [[f"Co {i}", f"T{i}"] for i in range(503)]).to_excel(
        buf, header=False, index=False)
    xlsx = buf.getvalue()

    class R:
        def __init__(self, status, content=b"", ctype="text/html"):
            self.status_code, self.content, self.headers = status, content, {"content-type": ctype}

    def fake_get(url, **kw):
        if "refused" in url:
            return R(403)
        if "top10" in url:
            return R(200, ("<table><tr><th>Ticker</th></tr>" + "".join(f"<tr><td>A{i}</td></tr>" for i in range(10))
                           + "</table>").encode())
        return R(200, xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    monkeypatch.setattr(discover.httpx, "get", fake_get)

    async def two_bad(db, q):
        return [{"url": "https://issuer.test/refused.xlsx"}, {"url": "https://issuer.test/top10"}], "Claude Haiku 5.5"
    monkeypatch.setattr(discover, "model_candidates", two_bad)
    try:
        asyncio.run(discover.find_etf_source(None, "SPY", 480))
        raise AssertionError("expected an error")
    except universes.UniverseError as e:
        assert "refused.xlsx: HTTP 403 (the site doesn't allow automated downloads)" in str(e)
        assert "top10: only 10 tickers, expected at least 480" in str(e)

    async def good(db, q):
        return [{"url": "https://issuer.test/holdings-daily-us-en-spy.xlsx", "column": "Ticker"}], "Claude Haiku 5.5"
    monkeypatch.setattr(discover, "model_candidates", good)
    found = asyncio.run(discover.find_etf_source(None, "SPY", 480))
    assert len(found["tickers"]) == 503 and found["column"] == "Ticker"  # Excel read from the raw bytes
