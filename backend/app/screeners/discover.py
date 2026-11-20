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


def _try(url: str, columns: list[str], min_count: int) -> tuple[list[str], str] | None:
    """(tickers, column) if the URL yields a plausible list with one of the columns."""
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.info("Candidate %s failed: %s", url, e)
        return None
    best: tuple[list[str], str] | None = None
    for col in dict.fromkeys(columns):
        try:
            tickers = parse_source(resp.text, resp.headers.get("content-type", ""), url, col)
        except Exception:
            continue
        if len(tickers) >= min_count and (best is None or len(tickers) > len(best[0])):
            best = (tickers, col)
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


def _cheapest_claude(db: Session):
    from app.agents.loop import ModelConfig
    from app.llm.providers import model_available

    models = {t.slug: t for t in db.exec(select(Tool).where(Tool.kind == "model", Tool.enabled == True)).all()}  # noqa: E712
    for slug in MODEL_ORDER:
        t = models.get(slug)
        if t is not None and t.config.get("provider", "anthropic") == "anthropic" and model_available(t.config):
            return ModelConfig.from_tool_config(t.config), t.name
    return None, None


async def model_candidates(db: Session, query: str) -> tuple[list[dict[str, Any]], str | None]:
    """Candidates suggested by the cheapest Claude model with web search. ([], None) if none is available."""
    from app.agents.loop import client
    from app.llm.mock_loop import mock_mode

    if mock_mode():
        return [], None
    model, name = _cheapest_claude(db)
    if model is None:
        return [], None
    resp = await client().messages.create(
        model=model.model_id, max_tokens=1500,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": PROMPT.format(query=query)}])
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        cands = json.loads(m.group(0))["candidates"] if m else []
    except (ValueError, KeyError, TypeError):
        cands = []
    return [c for c in cands if isinstance(c, dict) and str(c.get("url", "")).startswith("http")], name


async def find_etf_source(db: Session, symbol: str) -> dict[str, Any]:
    """{url, column, tickers, found_by}: the ETF's verified holdings file/page. Raises UniverseError."""
    cands, model_name = await model_candidates(db, symbol)
    if model_name is None:
        raise UniverseError("no Claude model is available to search for the ETF's holdings file")
    for c in cands:
        got = _try(c["url"], [str(c.get("column") or ""), *TICKER_COLUMNS], min_count=5)
        if got:
            return {"url": c["url"], "column": got[1], "tickers": got[0], "found_by": f"web search ({model_name})"}
    raise UniverseError(f"no downloadable holdings list found for {symbol!r}")


def find_wikipedia_source(query: str) -> dict[str, Any] | None:
    """{url, column, tickers, found_by} for a Wikipedia members table, or None (free)."""
    for url in wikipedia_candidates(query):
        got = _try(url, list(TICKER_COLUMNS), min_count=10)
        if got:
            return {"url": url, "column": got[1], "tickers": got[0], "found_by": "Wikipedia search"}
    return None


async def find_sources(db: Session, symbol: str, index_name: str = "") -> dict[str, Any]:
    """{"main": ... or None, "backup": ... or None, "main_error": str | None}. Raises if neither is found."""
    symbol = symbol.strip()
    if not symbol and not index_name.strip():
        raise UniverseError("Enter the ETF symbol (or index name) first.")
    main, main_error = None, None
    if symbol:
        try:
            main = await find_etf_source(db, symbol)
        except UniverseError as e:
            main_error = str(e)
    query = re.sub(r"\(.*?\)", "", index_name).strip() or symbol
    backup = find_wikipedia_source(query)
    if not main and not backup:
        raise UniverseError(f"Couldn't find a source for {symbol or query!r} ({main_error or 'nothing on Wikipedia'}). "
                            "Enter the page or file address yourself.")
    return {"main": main, "backup": backup, "main_error": main_error}
