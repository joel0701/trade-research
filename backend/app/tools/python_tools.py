"""Built-in Python tools. Each has a JSON schema and an async implementation.

Market data here comes from Yahoo Finance (yfinance), which is free and fine for local
research. For real-time and account data, add the IBKR MCP server in the Tools screen.
Register new functions in PYTHON_TOOLS; then add a Tool row with kind=python.
"""
import asyncio
import json
import time
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.tools.indicators import snapshot


@dataclass
class PythonTool:
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Awaitable[str]]


def _ticker_schema(extra: dict | None = None, required: list[str] | None = None) -> dict:
    props = {"ticker": {"type": "string", "description": "Stock symbol, e.g. AAPL"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": ["ticker", *(required or [])]}


def _yf():
    import yfinance as yf  # imported lazily: slow import

    return yf


async def price_history(ticker: str, period: str = "6mo", interval: str = "1d") -> str:
    def run() -> str:
        df = _yf().Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return f"No price data for {ticker}."
        df = df[["Open", "High", "Low", "Close", "Volume"]].round(4)
        rows = df.tail(60)  # keep tool results compact
        return (
            f"{ticker} {interval} bars, last {len(rows)} of {len(df)} ({period}):\n"
            + rows.to_csv(date_format="%Y-%m-%d")
        )

    return await asyncio.to_thread(run)


async def technical_snapshot(ticker: str) -> str:
    def run() -> str:
        df = _yf().Ticker(ticker).history(period="2y", interval="1d", auto_adjust=True)
        if len(df) < 30:
            return f"Not enough price history for {ticker}."
        return json.dumps({"ticker": ticker, **snapshot(df)})

    return await asyncio.to_thread(run)


_FUNDAMENTAL_KEYS = [
    "longName", "sector", "industry", "marketCap", "enterpriseValue", "trailingPE", "forwardPE",
    "pegRatio", "priceToBook", "priceToSalesTrailing12Months", "enterpriseToEbitda",
    "profitMargins", "operatingMargins", "grossMargins", "returnOnEquity", "returnOnAssets",
    "revenueGrowth", "earningsGrowth", "totalRevenue", "ebitda", "freeCashflow", "totalCash",
    "totalDebt", "debtToEquity", "currentRatio", "dividendYield", "payoutRatio", "beta",
    "sharesOutstanding", "shortPercentOfFloat", "heldPercentInstitutions",
    "targetMeanPrice", "recommendationKey", "numberOfAnalystOpinions",
]


async def fundamentals(ticker: str) -> str:
    def run() -> str:
        info = _yf().Ticker(ticker).info or {}
        data = {k: info.get(k) for k in _FUNDAMENTAL_KEYS if info.get(k) is not None}
        return json.dumps({"ticker": ticker, **data}) if data else f"No fundamentals for {ticker}."

    return await asyncio.to_thread(run)


async def options_chain(ticker: str, expiry: str | None = None, strikes_around_spot: int = 10) -> str:
    def run() -> str:
        t = _yf().Ticker(ticker)
        expiries = list(t.options or [])
        if not expiries:
            return f"No listed options for {ticker}."
        exp = expiry if expiry in expiries else expiries[0]
        spot = float(t.history(period="5d")["Close"].iloc[-1])
        chain = t.option_chain(exp)
        cols = ["strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"]

        def near(df: pd.DataFrame) -> str:
            df = df.assign(dist=(df["strike"] - spot).abs()).nsmallest(strikes_around_spot * 2, "dist")
            return df.sort_values("strike")[cols].round(4).to_csv(index=False)

        return (
            f"{ticker} spot {spot:.2f}, expiry {exp}. Other expiries: {', '.join(expiries[:12])}\n"
            f"CALLS:\n{near(chain.calls)}\nPUTS:\n{near(chain.puts)}"
        )

    return await asyncio.to_thread(run)


_ddg_lock = threading.Lock()
_ddg_last = [0.0]
DDG_MIN_INTERVAL = 1.0   # seconds between searches, app-wide (specialists search in parallel)
DDG_RETRIES = 2
DDG_KEEP = ("title", "href", "url", "body", "date", "source")


def _ddg_once(query: str, max_results: int, news: bool) -> list[dict]:
    from ddgs import DDGS

    with _ddg_lock:  # one search at a time, spaced out, to avoid DuckDuckGo's rate limits
        wait = DDG_MIN_INTERVAL - (time.monotonic() - _ddg_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with DDGS(timeout=10) as d:
                return list(d.news(query, max_results=max_results) if news else d.text(query, max_results=max_results))
        finally:
            _ddg_last[0] = time.monotonic()


async def ddg_search(query: str, max_results: int = 8, news: bool = False) -> str:
    def run() -> str:
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

        max_n = max(1, min(int(max_results or 8), 20))
        for attempt in range(DDG_RETRIES + 1):
            try:
                hits = _ddg_once(query, max_n, news)
                break
            except (RatelimitException, TimeoutException) as e:
                if attempt == DDG_RETRIES:
                    kind = "rate-limited" if isinstance(e, RatelimitException) else "timed out"
                    return (f"DuckDuckGo search {kind} after {DDG_RETRIES + 1} tries. Use another search tool "
                            "(e.g. web_search) or try again later.")
                time.sleep(1.5 * (attempt + 1))
            except DDGSException as e:
                if "no results" in str(e).lower():  # ddgs raises when nothing matches
                    return "No results."
                if attempt == DDG_RETRIES:
                    return f"DuckDuckGo search failed: {e}. Use another search tool (e.g. web_search) or try again later."
                time.sleep(1.5 * (attempt + 1))
        rows = [{k: h[k] for k in DDG_KEEP if h.get(k)} for h in hits]
        return json.dumps(rows, default=str) if rows else "No results."

    return await asyncio.to_thread(run)


from app.tools import finra, ibkr_gateway, massive, sec_edgar  # noqa: E402

PYTHON_TOOLS: dict[str, PythonTool] = {
    "ibkr_quote": PythonTool(
        "Current quote from Interactive Brokers: last, bid/ask with sizes, high/low, volume, previous close "
        "(live if subscribed, otherwise delayed; the result says which).",
        _ticker_schema(), ibkr_gateway.ibkr_quote),
    "ibkr_bars": PythonTool(
        "Historical price bars from Interactive Brokers (most recent 120 returned).",
        _ticker_schema({
            "duration": {"type": "string", "description": "How far back, e.g. '5 D', '3 M', '1 Y' (default '6 M')"},
            "bar_size": {"type": "string", "description": "1 min, 5 mins, 15 mins, 30 mins, 1 hour, 4 hours, 1 day, 1 week, 1 month"},
            "regular_hours_only": {"type": "boolean", "description": "Exclude pre/post-market (default true)"},
        }), ibkr_gateway.ibkr_bars),
    "ibkr_option_chain": PythonTool(
        "Option chain from Interactive Brokers for one expiry: bid/ask, last, volume, model IV and Greeks for "
        "strikes near the money, plus the list of upcoming expirations. Defaults to the nearest expiry.",
        {"type": "object", "properties": {
            "underlying": {"type": "string", "description": "Underlying stock symbol"},
            "expiration": {"type": "string", "description": "YYYY-MM-DD; empty = nearest"},
            "right": {"type": "string", "description": "C, P, or empty for both"},
            "strikes_around_spot": {"type": "integer", "description": "Strikes each side of spot (default 8)"},
        }, "required": ["underlying"]}, ibkr_gateway.ibkr_option_chain),
    "ibkr_scanner": PythonTool(
        "Run an Interactive Brokers market scanner over US stocks and get the ranked list. scan_code options: "
        + ", ".join(f"{k} ({v})" for k, v in ibkr_gateway.SCAN_CODES.items()) + ".",
        {"type": "object", "properties": {
            "scan_code": {"type": "string", "description": "IBKR scan code, e.g. TOP_PERC_GAIN or HOT_BY_OPT_VOLUME"},
            "location": {"type": "string", "description": "STK.US.MAJOR (default), STK.US, STK.NASDAQ or STK.NYSE"},
            "rows": {"type": "integer", "description": "How many results (default 25, max 50)"},
            "above_price": {"type": "number", "description": "Minimum share price"},
            "above_volume": {"type": "integer", "description": "Minimum daily volume (shares)"},
            "market_cap_above": {"type": "number", "description": "Minimum market cap in millions of USD"},
        }}, ibkr_gateway.ibkr_scanner),
    "ibkr_account": PythonTool(
        "Your Interactive Brokers account (read-only): balances, buying power, positions with P&L, and open orders.",
        {"type": "object", "properties": {}}, ibkr_gateway.ibkr_account),
    "finra_short_volume": PythonTool(
        "FINRA daily off-exchange short volume for a stock (the 'dark pool short ratio'): off-exchange volume, "
        "short volume and short % per day for recent trading days, with the recent trend. Free, updated daily.",
        _ticker_schema({"days": {"type": "integer", "description": "Trading days to cover (default 20, max 60)"}}),
        finra.finra_short_volume),
    "finra_ats_weekly": PythonTool(
        "FINRA weekly dark pool (ATS) data for a stock: shares, trades, dollar value and average trade size per "
        "week, the top dark pool venues, and dark pools' share of off-exchange volume. Published 2-4 weeks late.",
        _ticker_schema({"weeks": {"type": "integer", "description": "Weeks to cover (default 12, max 52)"}}),
        finra.finra_ats_weekly),
    "massive_quote": PythonTool(
        "Latest price, bid/ask, today's OHLCV and change from Massive.com (real-time or delayed per plan).",
        _ticker_schema(), massive.massive_quote),
    "massive_bars": PythonTool(
        "OHLCV price bars from Massive.com (minute, hour, day, week, month); the most recent 120 bars.",
        _ticker_schema({
            "timespan": {"type": "string", "description": "minute, hour, day, week or month (default day)"},
            "multiplier": {"type": "integer", "description": "Bar size multiple, e.g. 5 with minute = 5-minute bars"},
            "days": {"type": "integer", "description": "How many calendar days back (default 180)"},
        }), massive.massive_bars),
    "massive_option_chain": PythonTool(
        "Option chain snapshot from Massive.com for one expiry: bid/ask, volume, open interest, implied "
        "volatility and Greeks, for strikes near the money. Defaults to the nearest expiry.",
        {"type": "object", "properties": {
            "underlying": {"type": "string", "description": "Underlying stock symbol, e.g. EQT"},
            "expiration": {"type": "string", "description": "YYYY-MM-DD; empty = nearest expiry"},
            "contract_type": {"type": "string", "description": "call, put, or empty for both"},
            "strikes_around_spot": {"type": "integer", "description": "Strikes each side of spot (default 10)"},
        }, "required": ["underlying"]}, massive.massive_option_chain),
    "massive_news": PythonTool(
        "Recent news articles for a ticker from Massive.com, with per-ticker sentiment where available.",
        _ticker_schema({"limit": {"type": "integer", "description": "How many articles (default 10)"}}),
        massive.massive_news),
    "massive_offexchange": PythonTool(
        "Dark pool / off-exchange activity from Massive.com trades: share of volume reported to FINRA TRFs "
        "and the largest off-exchange prints, for today (or a given day).",
        _ticker_schema({"day": {"type": "string", "description": "YYYY-MM-DD; empty = most recent trades"}}),
        massive.massive_offexchange),
    "sec_filings": PythonTool(
        "Recent SEC EDGAR filings for a company (10-K, 10-Q, 8-K, S-1, DEF 14A, 13D/G...), with links. "
        "Use sec_filing_text to read one.",
        _ticker_schema({
            "forms": {"type": "string", "description": "Comma-separated form types, e.g. '10-K,10-Q,8-K'. Empty = all."},
            "limit": {"type": "integer", "description": "How many filings (default 10, max 50)"},
        }),
        sec_edgar.sec_filings,
    ),
    "sec_filing_text": PythonTool(
        "Read an SEC filing document as plain text. Without find/offset it lists the filing's sections first; "
        "use find='Risk Factors' (or another heading) to jump to a section, and offset to continue reading. "
        "Takes a URL returned by sec_filings.",
        {"type": "object", "properties": {
            "url": {"type": "string", "description": "An https://www.sec.gov/Archives/... document URL"},
            "find": {"type": "string", "description": "Phrase to start reading from (optional)"},
            "max_chars": {"type": "integer", "description": "How much text to return (default 20000)"},
            "offset": {"type": "integer", "description": "Character offset to continue reading from"},
        }, "required": ["url"]},
        sec_edgar.sec_filing_text,
    ),
    "insider_trades": PythonTool(
        "Recent insider transactions from SEC Form 4 filings: who (role), buy or sell, shares, price, "
        "value, holdings after, plus totals for open-market buys and sales.",
        _ticker_schema({"filings": {"type": "integer", "description": "How many recent Form 4 filings to read (default 15)"}}),
        sec_edgar.insider_trades,
    ),
    "price_history": PythonTool(
        "Daily or intraday OHLCV price bars for a stock (most recent 60 bars returned).",
        _ticker_schema({
            "period": {"type": "string", "description": "1mo, 3mo, 6mo, 1y, 2y, 5y, ytd, max"},
            "interval": {"type": "string", "description": "1d, 1wk, 1h, 15m (intraday only for short periods)"},
        }),
        price_history,
    ),
    "technical_snapshot": PythonTool(
        "Latest technical indicators for a stock: SMAs, RSI, MACD, ATR, relative volume, "
        "% changes and distance from 52-week high.",
        _ticker_schema(),
        technical_snapshot,
    ),
    "fundamentals": PythonTool(
        "Key fundamentals and valuation for a stock: sector, margins, growth, multiples, "
        "balance sheet, analyst targets.",
        _ticker_schema(),
        fundamentals,
    ),
    "options_chain": PythonTool(
        "Options chain near the money for one expiry: bid/ask, volume, open interest, implied volatility.",
        _ticker_schema({
            "expiry": {"type": "string", "description": "YYYY-MM-DD; defaults to the nearest expiry"},
            "strikes_around_spot": {"type": "integer", "description": "Strikes each side of spot (default 10)"},
        }),
        options_chain,
    ),
    "ddg_search": PythonTool(
        "DuckDuckGo web or news search. Returns titles, URLs and snippets.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "news": {"type": "boolean", "description": "Search news instead of the web"},
            },
            "required": ["query"],
        },
        ddg_search,
    ),
}
