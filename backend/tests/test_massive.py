"""Massive.com tools against simulated API responses."""
import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.tools import massive


def install(monkeypatch, handler, seen):
    def wrapped(request):
        seen.append(request)
        return handler(request)
    real = httpx.AsyncClient
    monkeypatch.setattr(massive, "_client", lambda: real(transport=httpx.MockTransport(wrapped)))
    monkeypatch.setenv("MASSIVE_API_KEY", "mk_test")


def opt(strike, typ, exp="2026-07-17"):
    return {"details": {"ticker": f"O:EQT{exp}{typ[0].upper()}{strike}", "contract_type": typ,
                        "strike_price": strike, "expiration_date": exp},
            "greeks": {"delta": 0.5 if typ == "call" else -0.5, "gamma": 0.05, "theta": -0.03, "vega": 0.08},
            "last_quote": {"bid": 1.1, "ask": 1.2}, "last_trade": {"price": 1.15}, "day": {"volume": 300},
            "open_interest": 1200, "implied_volatility": 0.42, "underlying_asset": {"price": 52.4}}


def handler(request: httpx.Request) -> httpx.Response:
    p, q = request.url.path, dict(request.url.params)
    if p == "/v2/snapshot/locale/us/markets/stocks/tickers/EQT":
        return httpx.Response(200, json={"ticker": {"lastTrade": {"p": 52.4, "s": 100}, "lastQuote": {"p": 52.39, "s": 3, "P": 52.41, "S": 5},
                                                    "day": {"o": 51, "h": 53, "l": 50.5, "c": 52.4, "v": 9000000, "vw": 52.1},
                                                    "prevDay": {"c": 51.2}, "todaysChange": 1.2, "todaysChangePerc": 2.34}})
    if p.startswith("/v2/aggs/ticker/EQT/range/1/day/"):
        return httpx.Response(200, json={"results": [{"t": 1700000000000 + i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}
                                                     for i in range(200)]})
    if p == "/v3/snapshot/options/EQT":
        if "expiration_date.gte" in q:  # nearest-expiry probe
            return httpx.Response(200, json={"results": [opt(50, "call")]})
        assert q["expiration_date"] == "2026-07-17"
        if q.get("cursor") == "2":
            return httpx.Response(200, json={"results": [opt(s, "put") for s in (40, 50, 55, 60, 70)]})
        return httpx.Response(200, json={"results": [opt(s, "call") for s in (40, 50, 55, 60, 70)],
                                         "next_url": "https://api.massive.com/v3/snapshot/options/EQT?expiration_date=2026-07-17&cursor=2"})
    if p == "/v2/reference/news":
        return httpx.Response(200, json={"results": [{"published_utc": "2026-07-01T12:00:00Z", "title": "EQT beats", "article_url": "https://x",
                                                      "publisher": {"name": "Wire"}, "description": "Strong quarter",
                                                      "insights": [{"ticker": "EQT", "sentiment": "positive", "sentiment_reasoning": "beat"}]}]})
    if p == "/v3/trades/EQT":
        trades = [{"price": 52.0, "size": 100, "exchange": 11, "sip_timestamp": 1}, {"price": 52.1, "size": 300, "exchange": 4, "trf_id": 201, "sip_timestamp": 2},
                  {"price": 52.2, "size": 50000, "exchange": 4, "trf_id": 202, "sip_timestamp": 3}, {"price": 52.0, "size": 600, "exchange": 12, "sip_timestamp": 4}]
        return httpx.Response(200, json={"results": trades})
    if p == "/v2/snapshot/locale/us/markets/stocks/tickers/NOPE":
        return httpx.Response(403, json={"status": "NOT_AUTHORIZED", "message": "You are not entitled to this data."})
    return httpx.Response(404)


async def test_quote_bars_news(monkeypatch):
    seen = []
    install(monkeypatch, handler, seen)
    q = json.loads(await massive.massive_quote("eqt"))
    assert (q["last_price"], q["bid"], q["ask"], q["change_pct"]) == (52.4, 52.39, 52.41, 2.34)
    assert seen[0].headers["authorization"] == "Bearer mk_test" and seen[0].url.host == "api.massive.com"

    b = json.loads(await massive.massive_bars("EQT"))
    assert b["bars_total"] == 200 and len(b["bars"]) == 120
    assert "timespan must be" in await massive.massive_bars("EQT", timespan="decade")

    n = json.loads(await massive.massive_news("EQT"))
    assert n["articles"][0]["sentiment"] == "positive" and n["articles"][0]["publisher"] == "Wire"


async def test_option_chain_nearest_expiry_and_pagination(monkeypatch):
    install(monkeypatch, handler, [])
    ch = json.loads(await massive.massive_option_chain("EQT", strikes_around_spot=1))
    assert ch["expiration"] == "2026-07-17" and ch["spot"] == 52.4
    assert sorted({c["strike"] for c in ch["contracts"]}) == [50, 55]  # 2 strikes nearest 52.4
    assert {c["type"] for c in ch["contracts"]} == {"call", "put"}  # second page (puts) was fetched
    assert ch["contracts"][0]["iv"] == 0.42 and ch["contracts"][0]["delta"] in (0.5, -0.5)


async def test_off_exchange_share(monkeypatch):
    install(monkeypatch, handler, [])
    d = json.loads(await massive.massive_offexchange("EQT"))
    assert d["volume_total"] == 51000 and d["volume_off_exchange"] == 50300
    assert d["off_exchange_pct"] == 98.63 and d["largest_off_exchange_prints"][0]["size"] == 50000
    assert d["complete_day"] is True


async def test_plan_errors_and_missing_key(monkeypatch):
    install(monkeypatch, handler, [])
    assert "plan may not include" in await massive.massive_quote("NOPE")
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert "MASSIVE_API_KEY" in await massive.massive_quote("EQT")


def test_massive_connection_and_sources_seeded(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with TestClient(app) as c:
        conn = next(x for x in c.get("/api/connections").json() if x["slug"] == "massive")
        tools = {t["slug"]: t for t in c.get("/api/tools").json() if t["connection_id"] == conn["id"]}
        assert set(tools) == {"massive_quote", "massive_bars", "massive_option_chain", "massive_news", "massive_offexchange"}
        assert tools["massive_offexchange"]["capabilities"] == ["dark_pool"]
        status = {s["id"]: s for s in c.get("/api/tools-status").json()}
        assert status[tools["massive_quote"]["id"]]["reason"] == "set MASSIVE_API_KEY in .env"
        monkeypatch.setenv("MASSIVE_API_KEY", "mk")
        assert {s["id"]: s for s in c.get("/api/tools-status").json()}[tools["massive_quote"]["id"]]["available"]
