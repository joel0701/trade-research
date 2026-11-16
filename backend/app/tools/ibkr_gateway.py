"""Interactive Brokers through IB Gateway (or TWS) running on this computer, via the TWS API.

No API key: you log in to IB Gateway and enable API access (Configure > Settings > API >
"Enable ActiveX and Socket Clients"; keep "Read-Only API" on). The backend keeps one read-only
connection and reconnects when IB Gateway comes back (e.g. after its daily restart).
Settings (.env): IBKR_HOST (default 127.0.0.1), IBKR_PORT (4002 paper / 4001 live IB Gateway,
7497 / 7496 for TWS), IBKR_CLIENT_ID (default 17), IBKR_MARKET_DATA_TYPE (default 3 = live
when subscribed, delayed otherwise; 1 = live only).
This module never places, changes or cancels orders.
"""
import asyncio
import json
import math
import os
import socket
from datetime import date
from typing import Any

TIMEOUT = 20  # seconds for any single request to IB Gateway


class GatewayError(Exception):
    pass


def settings() -> tuple[str, int, int]:
    return (os.getenv("IBKR_HOST", "127.0.0.1"), int(os.getenv("IBKR_PORT", "4002")),
            int(os.getenv("IBKR_CLIENT_ID", "17")))


def gateway_reachable(timeout: float = 0.3) -> bool:
    """Quick check that something is listening on the IB Gateway port."""
    host, port, _ = settings()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_ib = None
_lock = asyncio.Lock()


async def connection():
    """The shared read-only IB connection, (re)connecting if needed."""
    global _ib
    from ib_async import IB

    async with _lock:
        if _ib is not None and _ib.isConnected():
            return _ib
        host, port, client_id = settings()
        ib = IB()
        try:
            await ib.connectAsync(host, port, clientId=client_id, readonly=True, timeout=10)
        except Exception as e:
            raise GatewayError(
                f"IB Gateway isn't reachable at {host}:{port} ({type(e).__name__}). Start IB Gateway, log in, and "
                "enable API access (Configure > Settings > API > Enable ActiveX and Socket Clients). "
                "If you use a different port, set IBKR_PORT in .env.") from e
        ib.reqMarketDataType(int(os.getenv("IBKR_MARKET_DATA_TYPE", "3")))
        _ib = ib
        return ib


def _num(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) or f == -1 else round(f, 4)


DATA_TYPE = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed-frozen"}


async def _run(fn) -> str:
    try:
        return json.dumps(await asyncio.wait_for(fn(await connection()), timeout=TIMEOUT * 3), default=str)
    except GatewayError as e:
        return str(e)
    except asyncio.TimeoutError:
        return "IB Gateway didn't answer in time. It may be restarting or waiting for you to log in."


async def _stock(ib, symbol: str):
    from ib_async import Stock

    c = Stock(symbol.strip().upper(), "SMART", "USD")
    q = await ib.qualifyContractsAsync(c)
    if not q or q[0] is None or not getattr(q[0], "conId", 0):
        raise GatewayError(f"IBKR doesn't recognize {symbol.upper()} as a US stock.")
    return q[0]


async def ibkr_quote(ticker: str) -> str:
    async def go(ib):
        c = await _stock(ib, ticker)
        [t] = await asyncio.wait_for(ib.reqTickersAsync(c), TIMEOUT)
        return {"ticker": c.symbol, "exchange": c.primaryExchange or c.exchange,
                "last": _num(t.last), "bid": _num(t.bid), "bid_size": _num(t.bidSize), "ask": _num(t.ask),
                "ask_size": _num(t.askSize), "high": _num(t.high), "low": _num(t.low), "volume": _num(t.volume),
                "prev_close": _num(t.close), "market_price": _num(t.marketPrice()),
                "data": DATA_TYPE.get(getattr(t, "marketDataType", 0), "unknown"), "time": t.time}
    return await _run(go)


BAR_SIZES = {"1 min", "5 mins", "15 mins", "30 mins", "1 hour", "4 hours", "1 day", "1 week", "1 month"}


async def ibkr_bars(ticker: str, duration: str = "6 M", bar_size: str = "1 day", regular_hours_only: bool = True) -> str:
    if bar_size not in BAR_SIZES:
        return f"bar_size must be one of: {', '.join(sorted(BAR_SIZES))}"

    async def go(ib):
        c = await _stock(ib, ticker)
        bars = await ib.reqHistoricalDataAsync(c, endDateTime="", durationStr=duration, barSizeSetting=bar_size,
                                               whatToShow="TRADES", useRTH=regular_hours_only, timeout=TIMEOUT)
        rows = [{"date": str(b.date), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume} for b in bars]
        return {"ticker": c.symbol, "duration": duration, "bar_size": bar_size, "bars_total": len(rows), "bars": rows[-120:]}
    return await _run(go)


async def ibkr_option_chain(underlying: str, expiration: str = "", right: str = "", strikes_around_spot: int = 8) -> str:
    """One expiry: bid/ask, last, volume and model IV/Greeks for strikes near the money."""
    from ib_async import Option

    async def go(ib):
        stk = await _stock(ib, underlying)
        [ut] = await asyncio.wait_for(ib.reqTickersAsync(stk), TIMEOUT)
        spot = _num(ut.marketPrice()) or _num(ut.last) or _num(ut.close)
        chains = await ib.reqSecDefOptParamsAsync(stk.symbol, "", stk.secType, stk.conId)
        chain = next((ch for ch in chains if ch.exchange == "SMART"), chains[0] if chains else None)
        if chain is None:
            raise GatewayError(f"No listed options for {stk.symbol}.")
        exps = sorted(e for e in chain.expirations if e >= date.today().strftime("%Y%m%d"))
        want = expiration.replace("-", "")
        exp = want if want in chain.expirations else (exps[0] if exps else None)
        if exp is None:
            raise GatewayError(f"No upcoming expirations for {stk.symbol}.")
        n = max(1, min(strikes_around_spot, 20))
        strikes = sorted(chain.strikes, key=lambda s: abs(s - spot) if spot else 0)[: n * 2] if spot else sorted(chain.strikes)[: n * 2]
        rights = [right.upper()[0]] if right.upper()[:1] in ("C", "P") else ["C", "P"]
        opts = [Option(stk.symbol, exp, k, r, "SMART", tradingClass=chain.tradingClass, multiplier=chain.multiplier)
                for k in sorted(strikes) for r in rights]
        qualified = [o for o in await ib.qualifyContractsAsync(*opts) if o is not None and getattr(o, "conId", 0)]
        rows = []
        for i in range(0, len(qualified), 50):  # snapshot in batches
            for t in await asyncio.wait_for(ib.reqTickersAsync(*qualified[i:i + 50]), TIMEOUT):
                g = t.modelGreeks
                rows.append({"strike": t.contract.strike, "right": t.contract.right, "bid": _num(t.bid), "ask": _num(t.ask),
                             "last": _num(t.last), "volume": _num(t.volume),
                             "iv": _num(g.impliedVol) if g else None, "delta": _num(g.delta) if g else None,
                             "gamma": _num(g.gamma) if g else None, "theta": _num(g.theta) if g else None,
                             "vega": _num(g.vega) if g else None})
        rows.sort(key=lambda r: (r["strike"], r["right"]))
        return {"underlying": stk.symbol, "spot": spot, "expiration": f"{exp[:4]}-{exp[4:6]}-{exp[6:]}",
                "other_expirations": [f"{e[:4]}-{e[4:6]}-{e[6:]}" for e in exps[:12]], "multiplier": chain.multiplier,
                "contracts": rows, "note": "Greeks are IBKR model values. Open interest isn't included in snapshots."}
    return await _run(go)


ACCOUNT_TAGS = ["NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds", "ExcessLiquidity",
                "GrossPositionValue", "UnrealizedPnL", "RealizedPnL", "MaintMarginReq", "InitMarginReq"]


async def ibkr_account() -> str:
    """Read-only account snapshot: balances, positions with P&L, and open orders."""
    async def go(ib):
        summary = {v.tag: _num(v.value) for v in await ib.accountSummaryAsync()
                   if v.tag in ACCOUNT_TAGS and v.currency in ("USD", "BASE", "")}
        positions = []
        for p in ib.portfolio():
            c = p.contract
            positions.append({"symbol": c.symbol, "type": c.secType, "position": p.position,
                              "avg_cost": _num(p.averageCost), "market_price": _num(p.marketPrice),
                              "market_value": _num(p.marketValue), "unrealized_pnl": _num(p.unrealizedPNL),
                              "realized_pnl": _num(p.realizedPNL),
                              **({"expiry": c.lastTradeDateOrContractMonth, "strike": c.strike, "right": c.right}
                                 if c.secType in ("OPT", "FOP") else {})})
        orders = [{"symbol": t.contract.symbol, "action": t.order.action, "quantity": t.order.totalQuantity,
                   "type": t.order.orderType, "limit": _num(t.order.lmtPrice), "status": t.orderStatus.status}
                  for t in await ib.reqAllOpenOrdersAsync()]
        return {"balances": summary, "positions": positions, "open_orders": orders,
                "note": "Read-only. This app cannot place, change or cancel orders."}
    return await _run(go)
