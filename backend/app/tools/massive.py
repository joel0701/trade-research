"""Massive.com (formerly Polygon.io) market data REST API.

Needs MASSIVE_API_KEY in .env (from https://massive.com/dashboard/keys). The key is sent as an
`Authorization: Bearer` header. MASSIVE_BASE_URL overrides the host (default api.massive.com;
api.polygon.io also still works). What each call returns depends on your Massive plan
(real-time vs delayed, options, trades); plan errors are reported in plain words.
"""
import json
import os
from datetime import date, timedelta
from typing import Any

import httpx

DEFAULT_BASE = "https://api.massive.com"
OFF_EXCHANGE_ID = 4  # FINRA trade reporting facilities: off-exchange prints, incl. dark pools


class MassiveError(Exception):
    pass


def _base() -> str:
    return os.getenv("MASSIVE_BASE_URL", DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    key = os.getenv("MASSIVE_API_KEY", "").strip()
    if not key:
        raise MassiveError("Massive.com needs MASSIVE_API_KEY in .env (from massive.com/dashboard/keys). "
                           "Restart the backend after setting it.")
    return {"Authorization": f"Bearer {key}"}


async def _get(client: httpx.AsyncClient, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = path_or_url if path_or_url.startswith("http") else _base() + path_or_url
    r = await client.get(url, params=params, headers=_headers(), timeout=30)
    if r.status_code in (401, 403):
        try:
            msg = r.json().get("message") or r.json().get("error") or ""
        except ValueError:
            msg = ""
        raise MassiveError(f"Massive.com refused this request ({r.status_code}): {msg or 'not authorized'}. "
                           "Your plan may not include this data (e.g. real-time, options or trades).")
    if r.status_code == 429:
        raise MassiveError("Massive.com rate limit reached; try again shortly (free plans allow few calls per minute).")
    r.raise_for_status()
    return r.json()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


async def _run(fn) -> str:
    try:
        async with _client() as client:
            return json.dumps(await fn(client), default=str)
    except MassiveError as e:
        return str(e)


async def massive_quote(ticker: str) -> str:
    """Latest trade, bid/ask and today's bar from the stock snapshot."""
    t = ticker.strip().upper()

    async def go(c):
        d = (await _get(c, f"/v2/snapshot/locale/us/markets/stocks/tickers/{t}")).get("ticker") or {}
        if not d:
            raise MassiveError(f"No snapshot for {t}.")
        last, quote, day, prev = d.get("lastTrade", {}), d.get("lastQuote", {}), d.get("day", {}), d.get("prevDay", {})
        return {"ticker": t, "last_price": last.get("p"), "last_size": last.get("s"),
                "bid": quote.get("p"), "bid_size": quote.get("s"), "ask": quote.get("P"), "ask_size": quote.get("S"),
                "day": {"open": day.get("o"), "high": day.get("h"), "low": day.get("l"), "close": day.get("c"),
                        "volume": day.get("v"), "vwap": day.get("vw")},
                "prev_close": prev.get("c"), "change": d.get("todaysChange"), "change_pct": d.get("todaysChangePerc"),
                "updated_ns": d.get("updated")}
    return await _run(go)


async def massive_bars(ticker: str, timespan: str = "day", multiplier: int = 1, days: int = 180) -> str:
    """OHLCV bars (minute/hour/day/week), most recent 120 shown."""
    t = ticker.strip().upper()
    if timespan not in {"minute", "hour", "day", "week", "month"}:
        return "timespan must be one of minute, hour, day, week, month."
    end = date.today()
    start = end - timedelta(days=max(1, min(days, 3650)))

    async def go(c):
        data = await _get(c, f"/v2/aggs/ticker/{t}/range/{int(multiplier)}/{timespan}/{start}/{end}",
                          {"adjusted": "true", "sort": "asc", "limit": 50000})
        rows = data.get("results") or []
        bars = [{"t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"], "vw": r.get("vw")}
                for r in rows[-120:]]
        return {"ticker": t, "timespan": f"{multiplier} {timespan}", "from": str(start), "to": str(end),
                "bars_total": len(rows), "bars": bars, "note": "t is epoch milliseconds (UTC)"}
    return await _run(go)


async def massive_option_chain(underlying: str, expiration: str = "", contract_type: str = "",
                               strikes_around_spot: int = 10) -> str:
    """Option chain snapshot for one expiry: bid/ask, volume, open interest, IV and Greeks."""
    u = underlying.strip().upper()

    async def go(c):
        params: dict[str, Any] = {"limit": 250, "sort": "expiration_date", "order": "asc"}
        if contract_type in ("call", "put"):
            params["contract_type"] = contract_type
        if expiration:
            params["expiration_date"] = expiration
        else:  # find the nearest expiry from today on
            params["expiration_date.gte"] = str(date.today())
        data = await _get(c, f"/v3/snapshot/options/{u}", params)
        results = data.get("results") or []
        if not results:
            raise MassiveError(f"No option contracts found for {u}{' expiring ' + expiration if expiration else ''}.")
        exp = expiration or results[0]["details"]["expiration_date"]
        # Fetch the whole chosen expiry (follow pagination, bounded).
        params = {k: v for k, v in params.items() if not k.startswith("expiration_date")} | {"expiration_date": exp}
        data, rows, pages = await _get(c, f"/v3/snapshot/options/{u}", params), [], 0
        while True:
            rows += data.get("results") or []
            pages += 1
            if not data.get("next_url") or pages >= 8:
                break
            data = await _get(c, data["next_url"])
        spot = next((r.get("underlying_asset", {}).get("price") for r in rows
                     if r.get("underlying_asset", {}).get("price")), None)
        out = []
        for r in rows:
            det, g, q, day = r.get("details", {}), r.get("greeks") or {}, r.get("last_quote") or {}, r.get("day") or {}
            out.append({"contract": det.get("ticker"), "type": det.get("contract_type"), "strike": det.get("strike_price"),
                        "bid": q.get("bid"), "ask": q.get("ask"), "last": (r.get("last_trade") or {}).get("price"),
                        "volume": day.get("volume"), "open_interest": r.get("open_interest"),
                        "iv": r.get("implied_volatility"), "delta": g.get("delta"), "gamma": g.get("gamma"),
                        "theta": g.get("theta"), "vega": g.get("vega")})
        if spot:  # keep the strikes nearest the money, both sides
            n = max(1, strikes_around_spot)
            strikes = sorted({o["strike"] for o in out if o["strike"] is not None}, key=lambda s: abs(s - spot))[: n * 2]
            out = [o for o in out if o["strike"] in strikes]
        out.sort(key=lambda o: (o["strike"] or 0, o["type"] or ""))
        return {"underlying": u, "spot": spot, "expiration": exp, "contracts": out}
    return await _run(go)


async def massive_news(ticker: str, limit: int = 10) -> str:
    """Recent news for a ticker, with Massive's per-ticker sentiment insights when available."""
    t = ticker.strip().upper()

    async def go(c):
        data = await _get(c, "/v2/reference/news", {"ticker": t, "limit": max(1, min(limit, 50)),
                                                    "order": "desc", "sort": "published_utc"})
        items = []
        for a in data.get("results") or []:
            insight = next((i for i in a.get("insights") or [] if i.get("ticker") == t), None)
            items.append({"published": a.get("published_utc"), "title": a.get("title"),
                          "publisher": (a.get("publisher") or {}).get("name"), "url": a.get("article_url"),
                          "summary": (a.get("description") or "")[:500],
                          "sentiment": insight.get("sentiment") if insight else None,
                          "sentiment_reason": insight.get("sentiment_reasoning") if insight else None})
        return {"ticker": t, "articles": items}
    return await _run(go)


async def massive_offexchange(ticker: str, day: str = "", max_trades: int = 150000) -> str:
    """Off-exchange (FINRA TRF) share of recent trades: dark pool and internalized volume, plus largest prints."""
    t = ticker.strip().upper()

    async def go(c):
        params: dict[str, Any] = {"limit": 50000, "order": "desc", "sort": "timestamp"}
        if day:
            params["timestamp"] = day
        data, trades, pages = await _get(c, f"/v3/trades/{t}", params), [], 0
        while True:
            trades += data.get("results") or []
            pages += 1
            if not data.get("next_url") or len(trades) >= max_trades or pages >= 10:
                break
            data = await _get(c, data["next_url"])
        if not trades:
            raise MassiveError(f"No trades returned for {t}{' on ' + day if day else ''}.")
        total = sum(x.get("size", 0) for x in trades)
        off = [x for x in trades if x.get("exchange") == OFF_EXCHANGE_ID]
        off_vol = sum(x.get("size", 0) for x in off)
        biggest = sorted(off, key=lambda x: x.get("size", 0) * x.get("price", 0), reverse=True)[:15]
        times = [x.get("sip_timestamp") or x.get("participant_timestamp") for x in trades]
        return {
            "ticker": t, "trades_analyzed": len(trades), "complete_day": not data.get("next_url"),
            "window_ns": [min(times), max(times)] if times else None,
            "volume_total": total, "volume_off_exchange": off_vol,
            "off_exchange_pct": round(100 * off_vol / total, 2) if total else None,
            "largest_off_exchange_prints": [{"time_ns": x.get("sip_timestamp"), "price": x.get("price"), "size": x.get("size"),
                                             "notional": round(x.get("size", 0) * x.get("price", 0), 2),
                                             "trf_id": x.get("trf_id")} for x in biggest],
            "note": "Off-exchange = trades reported to a FINRA TRF (dark pools and broker internalization). "
                    "Times are epoch nanoseconds (UTC). If complete_day is false, figures cover the most recent trades only.",
        }
    return await _run(go)
