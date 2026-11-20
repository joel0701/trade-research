"""Index universes for rule-based scanners, and batched, cached Yahoo downloads."""
import json
import time

from fastapi.testclient import TestClient

from app.main import app
from app.screeners import engine, universes

PAGE = """<html><body>
<table><tr><th>Note</th></tr><tr><td>intro</td></tr></table>
<table><thead><tr><th>Symbol</th><th>Security</th></tr></thead><tbody>
<tr><td>MMM</td><td>3M</td></tr><tr><td>BRK.B</td><td>Berkshire</td></tr><tr><td>AAPL</td><td>Apple</td></tr>
</tbody></table></body></html>"""


def test_parse_constituents_picks_the_right_table_and_yahoo_format():
    assert universes.parse_constituents(PAGE, ("Symbol",), (2, 10)) == ["MMM", "BRK-B", "AAPL"]


def test_index_list_is_cached_and_falls_back_to_the_saved_list(monkeypatch, tmp_path):
    monkeypatch.setattr(universes, "CACHE_DIR", tmp_path)
    calls = []

    def fetch(key):
        calls.append(key)
        return ["AAA", "BBB"]
    monkeypatch.setattr(universes, "fetch_index", fetch)
    assert universes.get_index("dow30") == (["AAA", "BBB"], None)
    assert universes.get_index("dow30") == (["AAA", "BBB"], None) and calls == ["dow30"]  # cached
    data = json.loads((tmp_path / "index_dow30.json").read_text())
    data["fetched_at"] = time.time() - 30 * 24 * 3600  # a month old: refresh, which fails
    (tmp_path / "index_dow30.json").write_text(json.dumps(data))

    def broken(key):
        raise OSError("offline")
    monkeypatch.setattr(universes, "fetch_index", broken)
    tickers, note = universes.get_index("dow30")
    assert tickers == ["AAA", "BBB"] and note.startswith("Couldn't refresh the Dow 30 list; using the one saved on")
    (tmp_path / "index_dow30.json").unlink()
    try:
        universes.get_index("dow30")
        raise AssertionError("expected an error")
    except universes.UniverseError as e:
        assert "custom ticker list" in str(e)


def test_scanner_runs_on_an_index_plus_extra_tickers(monkeypatch):
    members = [f"T{i:03d}" for i in range(503)]
    monkeypatch.setattr(universes, "get_index", lambda key: (members, None))
    seen = {}

    def fake_fetch(tickers):
        seen["tickers"] = tickers
        return {t: {"close": 10, "sma_200": 5, "rsi_14": 30 + (i % 20)} for i, t in enumerate(tickers)}
    monkeypatch.setattr(engine, "fetch_snapshots", fake_fetch)
    with TestClient(app) as c:
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "oversold_uptrend")
        assert {u["key"] for u in c.get("/api/universes").json()} == {"sp500", "nasdaq100", "dow30"}
        c.put(f"/api/screeners/{sc['id']}", json={"universe_index": "sp500", "universe": ["T001", "COIN"]})
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        assert len(seen["tickers"]) == 504 and seen["tickers"][-1] == "COIN"  # index + the extra ticker, no duplicates
        ins = c.get(f"/api/sessions/{r['session_id']}").json()["messages"][0]["text"]
        assert "- Universe: **S&P 500** (503 tickers)" in ins and "- Extra tickers: **COIN**" in ins
        assert "T250" not in ins  # the run card doesn't list all 503
        # This run only: the custom list instead of the index.
        r = c.post(f"/api/screeners/{sc['id']}/run", json={"universe_index": "", "universe": ["AAPL"]}).json()
        assert seen["tickers"] == ["AAPL"]
        c.put(f"/api/screeners/{sc['id']}", json={"universe_index": None, "universe": sc["universe"]})


def test_downloads_are_batched_and_cached(monkeypatch):
    engine._snap_cache.clear()
    batches = []

    def download(tickers):
        batches.append(len(tickers))
        return {t: {"close": 1} for t in tickers}
    monkeypatch.setattr(engine, "_download", download)
    tickers = [f"S{i}" for i in range(250)]
    assert len(engine.fetch_snapshots(tickers)) == 250 and batches == [100, 100, 50]
    assert len(engine.fetch_snapshots(tickers + ["NEW"])) == 251 and batches[-1] == 1  # only the new one downloaded
    engine._snap_cache.clear()
