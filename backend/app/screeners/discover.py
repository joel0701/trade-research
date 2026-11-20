"""Find where to download a stock list's members ("Find source" in Tools → Stock lists).

- Main source: the ETF's official holdings file (or page) from the fund issuer, found by the cheapest
  available Claude model with web search (issuer sites aren't searchable for free).
- Backup source: a Wikipedia table of the index's members, found with Wikipedia's free search API.

Every candidate is verified by downloading and parsing it; only a source that yields a plausible
ticker list is used. Refreshing later is plain code: the model is used only to find a source.
"""
import json
import logging
import re
from typing import Any

import httpx
from sqlmodel import Session, select

from app.models import Tool
from app.screeners.universes import HEADERS, TICKER_COLUMNS, UniverseError, parse_source

log = logging.getLogger(__name__)
WIKI_API = "https://en.wikipedia.org/w/api.php"
MODEL_ORDER = ["haiku", "sonnet", "opus", "fable"]  # cheapest first

PROMPT = """Find the official, downloadable list of ALL current holdings of the ETF "{query}" (or, if it's an
index, of the main ETF that tracks it), with ticker symbols, so a program can download it daily.

Prefer, in order:
1. The fund issuer's daily holdings download: a CSV or Excel (.xlsx) file at a stable address.
2. The issuer's holdings page, if it contains a full HTML table of tickers.
Don't use Wikipedia or news sites.

Reply with only a JSON object, no other text:
{{"candidates": [{{"url": "https://...", "column": "<name of the ticker column>", "expected_count": <number or null>}}]}}
Give up to 4 candidates, best first. Use exact URLs you found in search results."""


def _try(url: str, columns: list[str], min_count: int, why: list[str] | None = None) -> tuple[list[str], str] | None:
    """(tickers, column) if the URL yields a plausible list with one of the columns. Reasons for failure
    are appended to `why` (shown to the user and logged)."""
    short = url if len(url) <= 90 else url[:87] + "…"

    def fail(reason: str) -> None:
        log.info("Source candidate %s: %s", url, reason)
        if why is not None:
            why.append(f"{short}: {reason}")
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        fail(f"couldn't download ({type(e).__name__})")
        return None
    if resp.status_code >= 400:
        fail(f"HTTP {resp.status_code}" + (" (the site doesn't allow automated downloads)" if resp.status_code in (401, 403) else ""))
        return None
    best: tuple[list[str], str] | None = None
    most = 0
    for col in dict.fromkeys(c for c in columns if c):
        try:  # bytes, so Excel files parse correctly
            tickers = parse_source(resp.content, resp.headers.get("content-type", ""), url, col)
        except Exception:
            continue
        most = max(most, len(tickers))
        if len(tickers) >= min_count and (best is None or len(tickers) > len(best[0])):
            best = (tickers, col)
    if best is None:
        fail(f"only {most} tickers, expected at least {min_count}" if most else "no ticker table or column found")
    return best


def wikipedia_candidates(query: str) -> list[str]:
    """Wikipedia page addresses for the query (free search API)."""
    urls: list[str] = []
    for q in (f"{query} constituents", f"list of {query} components", query):
        try:
            data = httpx.get(WIKI_API, params={"action": "query", "list": "search", "srsearch": q, "format": "json",
                                               "srlimit": 3}, headers=HEADERS, timeout=15).json()
        except (httpx.HTTPError, ValueError):
            continue
        for hit in data.get("query", {}).get("search", []):
            url = "https://en.wikipedia.org/wiki/" + hit["title"].replace(" ", "_")
            if url not in urls:
                urls.append(url)
    return urls[:6]


def _search_model(db: Session):
    """(ModelConfig, name) for the web search: the low-level default if it's a Claude model, else the
    cheapest available Claude model (the search uses Claude's web search tool). Raises UniverseError
    saying why none can be used."""
    from app.agents.loop import ModelConfig
    from app.llm.mock_loop import mock_mode
    from app.llm.providers import PROVIDERS, model_available
    from app.llm.tiers import resolve, target_tool

    if mock_mode():
        raise UniverseError("Test mode is on, so no AI model is used; turn it off to search for the ETF's holdings file")
    low = target_tool("low")
    if low is not None and low.enabled and low.config.get("provider", "anthropic") == "anthropic" and model_available(low.config):
        return ModelConfig.from_tool_config(low.config), low.name
    models = {t.slug: t for t in db.exec(select(Tool).where(Tool.kind == "model", Tool.enabled == True)).all()}  # noqa: E712
    for slug in MODEL_ORDER:
        t = models.get(slug)
        if t is not None and resolve(t.config).get("provider", "anthropic") == "anthropic" and model_available(t.config):
            return ModelConfig.from_tool_config(t.config), t.name
    if not PROVIDERS["anthropic"].available():
        raise UniverseError("the ETF search uses Claude's web search, and ANTHROPIC_API_KEY isn't set in .env")
    raise UniverseError("the ETF search uses Claude's web search, and no Claude model is enabled in Tools → Models")


async def model_candidates(db: Session, query: str) -> tuple[list[dict[str, Any]], str]:
    """Candidates suggested by a Claude model with web search. Raises UniverseError if no model can be used."""
    from app.agents.loop import client

    model, name = _search_model(db)
    messages: list[dict[str, Any]] = [{"role": "user", "content": PROMPT.format(query=query)}]
    text = ""
    for _ in range(4):  # a long web-search turn can pause ("pause_turn"): send it back to continue
        resp = await client().messages.create(
            model=model.model_id, max_tokens=2000,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}], messages=messages)
        text += "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": [b.to_dict() for b in resp.content]})
    log.info("ETF source search for %s (%s) suggested: %s", query, name, text[-1500:])
    m = re.search(r"\{.*\}", text, re.S)
    try:
        cands = json.loads(m.group(0))["candidates"] if m else []
    except (ValueError, KeyError, TypeError):
        cands = []
    return [c for c in cands if isinstance(c, dict) and str(c.get("url", "")).startswith("http")], name


async def find_etf_source(db: Session, symbol: str, min_count: int = 0) -> dict[str, Any]:
    """{url, column, tickers, found_by}: the ETF's verified holdings file/page. Raises UniverseError."""
    cands, model_name = await model_candidates(db, symbol)  # raises if no model can be used
    why: list[str] = []
    import asyncio

    for c in cands:  # downloads run on a worker thread so the server keeps answering
        got = await asyncio.to_thread(_try, c["url"], [str(c.get("column") or ""), *TICKER_COLUMNS],
                                      max(5, min_count), why)
        if got:
            return {"url": c["url"], "column": got[1], "tickers": got[0], "found_by": f"web search ({model_name})"}
    if not cands:
        raise UniverseError(f"the web search ({model_name}) didn't suggest any holdings file for {symbol!r}")
    raise UniverseError(f"no suggested holdings file for {symbol!r} could be used. Tried: " + "; ".join(why))


def find_wikipedia_source(query: str, min_count: int = 0) -> dict[str, Any] | None:
    """{url, column, tickers, found_by} for a Wikipedia members table, or None (free)."""
    for url in wikipedia_candidates(query):
        got = _try(url, list(TICKER_COLUMNS), max(10, min_count))
        if got:
            return {"url": url, "column": got[1], "tickers": got[0], "found_by": "Wikipedia search"}
    return None


async def find_sources(db: Session, symbol: str, index_name: str = "", min_count: int = 0) -> dict[str, Any]:
    """{"main": ... or None, "backup": ... or None, "main_error": str | None}. Raises if neither is found."""
    symbol = symbol.strip()
    if not symbol and not index_name.strip():
        raise UniverseError("Enter the ETF symbol (or index name) first.")
    main, main_error = None, None
    if symbol:
        try:
            main = await find_etf_source(db, symbol, min_count)
        except UniverseError as e:
            main_error = str(e)
    query = re.sub(r"\(.*?\)", "", index_name).strip() or symbol
    import asyncio

    backup = await asyncio.to_thread(find_wikipedia_source, query, min_count)
    if not main and not backup:
        raise UniverseError(f"Couldn't find a source for {symbol or query!r} ({main_error or 'nothing on Wikipedia'}). "
                            "Enter the page or file address yourself.")
    return {"main": main, "backup": backup, "main_error": main_error}
