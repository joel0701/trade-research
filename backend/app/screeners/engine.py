"""Deterministic screener: computes indicator fields for a universe, then applies rules.

Rules format (nestable):
  {"all": [cond, ...]}   every condition must pass
  {"any": [cond, ...]}   at least one must pass
  {"not": cond}
  cond = {"field": "rsi_14", "op": "<", "value": 30}
       | {"field": "close", "op": ">", "field2": "sma_50"}          # compare two fields
       | {"field": "close", "op": "between", "value": [10, 100]}
Fields: see app.tools.indicators.snapshot (close, sma_50, rsi_14, rel_volume, ...).
"""
import asyncio
import logging
import operator
import time
from collections.abc import Callable
from typing import Any

import pandas as pd

from app.tools.indicators import snapshot

log = logging.getLogger(__name__)

OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
    "between": lambda a, b: b[0] <= a <= b[1],
}

Snapshot = dict[str, float | None]


def evaluate(rule: dict[str, Any], row: Snapshot) -> bool:
    if "all" in rule:
        return all(evaluate(r, row) for r in rule["all"])
    if "any" in rule:
        return any(evaluate(r, row) for r in rule["any"])
    if "not" in rule:
        return not evaluate(rule["not"], row)
    if not rule:
        return True
    left = row.get(rule["field"])
    right = row.get(rule["field2"]) if "field2" in rule else rule.get("value")
    if left is None or right is None:
        return False  # missing data never passes a rule
    op = OPS.get(rule["op"])
    if op is None:
        raise ValueError(f"Unknown operator {rule['op']!r}")
    return bool(op(left, right))


BATCH = 100            # tickers per Yahoo download (large lists in one request get throttled)
SNAPSHOT_TTL = 15 * 60  # seconds a ticker's computed snapshot is reused (re-running a scan is instant)
_snap_cache: dict[str, tuple[float, Snapshot]] = {}


def _download(tickers: list[str]) -> dict[str, Snapshot]:
    import yfinance as yf

    data = yf.download(tickers, period="2y", interval="1d", group_by="ticker",
                       auto_adjust=True, progress=False, threads=True)
    out: dict[str, Snapshot] = {}
    for t in tickers:
        try:
            df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
            df = df.dropna(subset=["Close"])
            if len(df) >= 30:
                out[t] = snapshot(df)
        except Exception:
            log.warning("No data for %s", t)
    return out


def fetch_snapshots(tickers: list[str]) -> dict[str, Snapshot]:
    """Two years of daily bars per ticker (Yahoo, in batches), turned into snapshot fields. Results are
    reused for SNAPSHOT_TTL, so running a large scan again is quick."""
    if not tickers:
        return {}
    now = time.monotonic()
    out = {t: s for t in tickers if (hit := _snap_cache.get(t)) and now - hit[0] < SNAPSHOT_TTL for s in [hit[1]]}
    todo = [t for t in dict.fromkeys(tickers) if t not in out]
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            got = _download(batch)
        except Exception:
            log.warning("Yahoo download failed for %d tickers starting %s", len(batch), batch[0], exc_info=True)
            continue
        for t, s in got.items():
            _snap_cache[t] = (time.monotonic(), s)
        out.update(got)
    return out


def screen(snapshots: dict[str, Snapshot], rules: dict[str, Any], sort_by: str | None = None,
           limit: int = 25) -> list[dict[str, Any]]:
    rows = [{"ticker": t, **s} for t, s in snapshots.items() if evaluate(rules, s)]
    if sort_by:
        desc = sort_by.startswith("-")
        key = sort_by.lstrip("-")
        rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0) if desc else (r.get(key) or 0)))
    return rows[:limit]


async def run_screener(universe: list[str], rules: dict[str, Any], sort_by: str | None, limit: int,
                       fetch: Callable[[list[str]], dict[str, Snapshot]] | None = None) -> list[dict[str, Any]]:
    snaps = await asyncio.to_thread(fetch or fetch_snapshots, universe)  # looked up at call time
    return screen(snaps, rules, sort_by, limit)


async def run_ibkr_scanner(params: dict[str, Any], rules: dict[str, Any], sort_by: str | None, limit: int,
                           fetch: Callable[[list[str]], dict[str, Snapshot]] | None = None) -> list[dict[str, Any]]:
    """Run an IBKR market scanner, add indicator fields, then apply optional rules."""
    from app.tools.ibkr_gateway import run_scan

    allowed = {"scan_code", "location", "rows", "above_price", "above_volume", "market_cap_above",
               "average_option_volume_above"}
    results = await run_scan(**{k: v for k, v in params.items() if k in allowed})
    try:
        fetch = fetch or fetch_snapshots  # looked up at call time
        snaps = await asyncio.to_thread(fetch, [r["ticker"] for r in results]) if results else {}
    except Exception:
        log.warning("Couldn't add indicator data to IBKR scanner results", exc_info=True)
        snaps = {}
    rows = [{**r, **snaps.get(r["ticker"], {})} for r in results]
    if rules:
        rows = [r for r in rows if evaluate(rules, r)]
    if sort_by:
        return screen({r["ticker"]: {k: v for k, v in r.items() if k != "ticker"} for r in rows}, {}, sort_by, limit)
    return rows[:limit]


def results_to_markdown(rows: list[dict[str, Any]], cols: list[str] | None = None) -> str:
    if not rows:
        return "_No matches._"
    cols = cols or (["rank"] if any("rank" in r for r in rows) else []) + [
        "ticker", "close", "pct_change_1d", "pct_change_20d", "rsi_14", "rel_volume",
        "sma_50", "sma_200", "pct_from_52w_high"]
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = "\n".join("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |" for r in rows)
    return head + body
