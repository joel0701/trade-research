"""IB Gateway tools against a simulated IB connection (the real gateway runs on the user's computer)."""
import json
import math
from datetime import date, timedelta
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from app.main import app
from app.tools import ibkr_gateway as gw

NAN = math.nan
FUTURE = (date.today() + timedelta(days=10)).strftime("%Y%m%d")
LATER = (date.today() + timedelta(days=40)).strftime("%Y%m%d")
PAST = (date.today() - timedelta(days=5)).strftime("%Y%m%d")


def ticker(contract, **kw):
    base = dict(contract=contract, last=NAN, bid=NAN, ask=NAN, bidSize=NAN, askSize=NAN, high=NAN, low=NAN,
                volume=NAN, close=NAN, modelGreeks=None, marketDataType=3, time=None)
    base.update(kw)
    t = NS(**base)
    t.marketPrice = lambda: kw.get("last", NAN)
    return t


class FakeIB:
    def __init__(self):
        self.calls = []

    def isConnected(self):
        return True

    async def qualifyContractsAsync(self, *cs):
        out = []
        for c in cs:
            if getattr(c, "symbol", "") == "NOPE":
                out.append(None)
                continue
            c.conId = 123 if c.secType == "STK" else int(c.strike * 10)
            c.primaryExchange = "NYSE"
            out.append(None if c.secType == "OPT" and c.strike == 99 else c)  # one invalid option
        return out

    async def reqTickersAsync(self, *cs):
        self.calls.append(("tickers", len(cs)))
        out = []
        for c in cs:
            if c.secType == "STK":
                out.append(ticker(c, last=52.4, bid=52.39, ask=52.41, bidSize=3, askSize=5, high=53, low=50.5,
                                  volume=9_000_000, close=51.2, marketDataType=1))
            else:
                g = NS(impliedVol=0.42, delta=0.55 if c.right == "C" else -0.45, gamma=0.05, theta=-0.03, vega=0.08)
                out.append(ticker(c, bid=1.1, ask=1.2, last=1.15, volume=300, modelGreeks=g))
        return out

    async def reqHistoricalDataAsync(self, c, **kw):
        self.calls.append(("bars", kw["durationStr"], kw["barSizeSetting"]))
        return [NS(date=f"2026-01-{i % 28 + 1:02d}", open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(200)]

    async def reqSecDefOptParamsAsync(self, sym, fut, sectype, conid):
        return [NS(exchange="CBOE", tradingClass="X", multiplier="100", expirations={FUTURE}, strikes=[1]),
                NS(exchange="SMART", tradingClass="EQT", multiplier="100", expirations={PAST, FUTURE, LATER},
                   strikes=[40, 45, 50, 52.5, 55, 60, 99])]

    async def accountSummaryAsync(self):
        return [NS(tag="NetLiquidation", value="125000.5", currency="USD"), NS(tag="BuyingPower", value="250000", currency="USD"),
                NS(tag="NetLiquidation", value="1", currency="EUR"), NS(tag="Cushion", value="0.9", currency="")]

    def portfolio(self):
        return [NS(contract=NS(symbol="EQT", secType="STK"), position=100, averageCost=48.0, marketPrice=52.4,
                   marketValue=5240.0, unrealizedPNL=440.0, realizedPNL=0.0),
                NS(contract=NS(symbol="EQT", secType="OPT", lastTradeDateOrContractMonth=FUTURE, strike=55.0, right="C"),
                   position=-1, averageCost=120.0, marketPrice=1.15, marketValue=-115.0, unrealizedPNL=5.0, realizedPNL=0.0)]

    async def reqAllOpenOrdersAsync(self):
        return [NS(contract=NS(symbol="EQT"), order=NS(action="SELL", totalQuantity=100, orderType="LMT", lmtPrice=60.0),
                   orderStatus=NS(status="Submitted"))]


def use_fake(monkeypatch):
    ib = FakeIB()

    async def fake_connection():
        return ib
    monkeypatch.setattr(gw, "connection", fake_connection)
    return ib


async def test_quote_and_bars(monkeypatch):
    ib = use_fake(monkeypatch)
    q = json.loads(await gw.ibkr_quote("eqt"))
    assert (q["ticker"], q["last"], q["bid"], q["ask"], q["prev_close"], q["data"]) == ("EQT", 52.4, 52.39, 52.41, 51.2, "live")
    b = json.loads(await gw.ibkr_bars("EQT", duration="1 Y", bar_size="1 day"))
    assert b["bars_total"] == 200 and len(b["bars"]) == 120 and ("bars", "1 Y", "1 day") in ib.calls
    assert "bar_size must be" in await gw.ibkr_bars("EQT", bar_size="2 days")
    assert "doesn't recognize NOPE" in await gw.ibkr_quote("NOPE")


async def test_option_chain_picks_smart_and_nearest_expiry(monkeypatch):
    use_fake(monkeypatch)
    ch = json.loads(await gw.ibkr_option_chain("EQT", strikes_around_spot=2))
    assert ch["spot"] == 52.4 and ch["expiration"] == f"{FUTURE[:4]}-{FUTURE[4:6]}-{FUTURE[6:]}"
    # The 4 strikes nearest 52.4 (the invalid 99 strike is dropped by qualification anyway).
    assert sorted({c["strike"] for c in ch["contracts"]}) == [45, 50, 52.5, 55]
    assert {c["right"] for c in ch["contracts"]} == {"C", "P"} and ch["contracts"][0]["iv"] == 0.42
    assert PAST not in "".join(ch["other_expirations"]).replace("-", "")
    calls = json.loads(await gw.ibkr_option_chain("EQT", expiration=f"{LATER[:4]}-{LATER[4:6]}-{LATER[6:]}", right="call"))
    assert calls["expiration"].replace("-", "") == LATER and {c["right"] for c in calls["contracts"]} == {"C"}


async def test_account_is_read_only_snapshot(monkeypatch):
    use_fake(monkeypatch)
    a = json.loads(await gw.ibkr_account())
    assert a["balances"] == {"NetLiquidation": 125000.5, "BuyingPower": 250000.0}  # USD only, known tags only
    assert a["positions"][1]["strike"] == 55.0 and a["positions"][0]["unrealized_pnl"] == 440.0
    assert a["open_orders"][0]["status"] == "Submitted" and "cannot place" in a["note"]


async def test_gateway_down_message(monkeypatch):
    import ib_async

    async def refuse(self, *a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(ib_async.IB, "connectAsync", refuse)
    monkeypatch.setattr(gw, "_ib", None)
    monkeypatch.setenv("IBKR_PORT", "4999")
    msg = await gw.ibkr_quote("EQT")
    assert "IB Gateway isn't reachable at 127.0.0.1:4999" in msg and "Enable ActiveX and Socket Clients" in msg


def test_status_reflects_gateway(monkeypatch):
    with TestClient(app) as c:
        tools = {t["slug"]: t["id"] for t in c.get("/api/tools").json()}
        monkeypatch.setattr(gw, "gateway_reachable", lambda timeout=0.3: False)
        st = {s["id"]: s for s in c.get("/api/tools-status").json()}
        assert "IB Gateway isn't running" in st[tools["ibkr_gw_quote"]]["reason"]
        monkeypatch.setattr(gw, "gateway_reachable", lambda timeout=0.3: True)
        st = {s["id"]: s for s in c.get("/api/tools-status").json()}
        assert all(st[tools[s]]["available"] for s in ("ibkr_gw_quote", "ibkr_gw_bars", "ibkr_gw_options",
                                                        "ibkr_gw_account", "ibkr_gw_scanner"))
        conns = {x["slug"]: x for x in c.get("/api/connections").json()}
        assert conns["ib_gateway"]["kind"] == "tws" and "ibkr" not in conns  # old MCP placeholder removed


def scan_row(rank, sym, name):
    return NS(rank=rank, contractDetails=NS(contract=NS(symbol=sym, primaryExchange="NASDAQ", exchange="SMART"),
                                            longName=name, industry="Technology"))


class ScanIB(FakeIB):
    async def reqScannerDataAsync(self, sub):
        self.calls.append(("scan", sub.scanCode, sub.locationCode, sub.numberOfRows, sub.abovePrice, sub.marketCapAbove))
        if sub.scanCode == "BOGUS":
            raise ValueError("Scanner code BOGUS not found")
        return [scan_row(0, "CRWV", "CoreWeave"), scan_row(1, "NVDA", "NVIDIA"), scan_row(2, "AMD", "AMD")]


def use_scan_ib(monkeypatch):
    ib = ScanIB()

    async def fake_connection():
        return ib
    monkeypatch.setattr(gw, "connection", fake_connection)
    return ib


async def test_scanner_tool(monkeypatch):
    ib = use_scan_ib(monkeypatch)
    out = json.loads(await gw.ibkr_scanner("hot_by_opt_volume", rows=99, above_price=5, market_cap_above=1000))
    assert [r["ticker"] for r in out["results"]] == ["CRWV", "NVDA", "AMD"] and out["results"][0]["rank"] == 1
    assert out["label"] == "Unusual option volume"
    assert ib.calls[-1] == ("scan", "HOT_BY_OPT_VOLUME", "STK.US.MAJOR", 50, 5.0, 1000.0)  # rows capped at 50
    assert "IBKR scanner error" in await gw.ibkr_scanner("BOGUS")


def test_scanners_page_runs_ibkr_scanner(monkeypatch):
    use_scan_ib(monkeypatch)
    from app.screeners import engine
    snaps = {"CRWV": {"close": 100.0, "rsi_14": 72.0}, "NVDA": {"close": 180.0, "rsi_14": 55.0}}  # AMD: no data
    monkeypatch.setattr(engine, "fetch_snapshots", lambda tickers: {t: snaps[t] for t in tickers if t in snaps})
    with TestClient(app) as c:
        scanners = {s["slug"]: s for s in c.get("/api/screeners").json()}
        assert {"ibkr_top_gainers", "ibkr_unusual_options", "ibkr_iv_rich"} <= set(scanners)
        sc = scanners["ibkr_unusual_options"]
        assert sc["source"] == "ibkr" and sc["params"]["scan_code"] == "HOT_BY_OPT_VOLUME"

        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        assert [m["ticker"] for m in r["matches"]] == ["CRWV", "NVDA", "AMD"]  # IBKR rank order
        assert r["matches"][0]["rsi_14"] == 72.0 and "rank" in r["prompt"] and "IBKR scanner" in r["prompt"]
        chat = c.get(f"/api/sessions/{r['session_id']}").json()
        assert chat["title"].startswith("Scanner: IBKR: unusual option volume (3 results)")

        # Your rules filter IBKR's results; AMD has no indicator data so it can't pass.
        c.put(f"/api/screeners/{sc['id']}", json={"rules": {"field": "rsi_14", "op": "<", "value": 70}})
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        assert [m["ticker"] for m in r["matches"]] == ["NVDA"] and "filtered by your rules" in r["prompt"]

        opts = c.get("/api/scanner-options").json()
        assert {"code": "TOP_PERC_GAIN", "label": "Top % gainers"} in opts["scan_codes"]

        async def down():
            raise gw.GatewayError("IB Gateway isn't reachable at 127.0.0.1:4002")
        monkeypatch.setattr(gw, "connection", down)
        bad = c.post(f"/api/screeners/{sc['id']}/run")
        assert bad.status_code == 503 and "IB Gateway isn't reachable" in bad.json()["detail"]
