"""Index ticker lists for rule-based scanners (S&P 500, Nasdaq-100, Dow 30).

Constituents come from Wikipedia's index lists and are cached in data/cache for a week. If a refresh
fails, the last saved list is used (and the run says so). Tickers are converted to Yahoo's format
(BRK.B -> BRK-B).
"""
import io
import json
import logging
import time
from pathlib import Path

import httpx

from app.config import CACHE_DIR

log = logging.getLogger(__name__)

MAX_AGE = 7 * 24 * 3600
HEADERS = {"User-Agent": "TradeResearch/1.0 (stock research app)"}

# key: (label, Wikipedia page, column names to look for, plausible row count)
INDEXES: dict[str, tuple[str, str, tuple[str, ...], tuple[int, int]]] = {
    "sp500": ("S&P 500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol", "Ticker"), (400, 520)),
    "nasdaq100": ("Nasdaq-100", "https://en.wikipedia.org/wiki/Nasdaq-100", ("Ticker", "Symbol"), (90, 110)),
    "dow30": ("Dow 30", "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", ("Symbol", "Ticker"), (28, 32)),
}


class UniverseError(Exception):
    pass


def _cache_file(key: str) -> Path:
    return CACHE_DIR / f"index_{key}.json"


def _read_cache(key: str) -> tuple[list[str], float] | None:
    try:
        data = json.loads(_cache_file(key).read_text())
        return data["tickers"], float(data["fetched_at"])
    except (OSError, ValueError, KeyError):
        return None


def to_yahoo(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


def parse_constituents(html: str, columns: tuple[str, ...], rows: tuple[int, int]) -> list[str]:
    """The ticker column of the first table on the page that looks like the constituent list."""
    import pandas as pd

    for table in pd.read_html(io.StringIO(html)):
        cols = [str(c[-1] if isinstance(c, tuple) else c).strip() for c in table.columns]
        for name in columns:
            if name in cols and rows[0] <= len(table) <= rows[1]:
                tickers = [to_yahoo(str(v)) for v in table.iloc[:, cols.index(name)].tolist() if str(v).strip()]
                return list(dict.fromkeys(t for t in tickers if t and t != "NAN"))
    raise UniverseError("couldn't find the constituents table on the page")


def fetch_index(key: str) -> list[str]:
    label, url, columns, rows = INDEXES[key]
    resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return parse_constituents(resp.text, columns, rows)


def get_index(key: str) -> tuple[list[str], str | None]:
    """(tickers, note). note is set when a stale saved list had to be used."""
    if key not in INDEXES:
        raise UniverseError(f"Unknown index {key!r}")
    label = INDEXES[key][0]
    cached = _read_cache(key)
    if cached and time.time() - cached[1] < MAX_AGE:
        return cached[0], None
    try:
        tickers = fetch_index(key)
    except Exception as e:  # network down, page changed…
        log.warning("Couldn't refresh the %s list: %s", label, e)
        if cached:
            day = time.strftime("%Y-%m-%d", time.localtime(cached[1]))
            return cached[0], f"Couldn't refresh the {label} list; using the one saved on {day}."
        raise UniverseError(f"Couldn't download the {label} list ({e}). Check the internet connection, or use a "
                            "custom ticker list.") from e
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_file(key).write_text(json.dumps({"tickers": tickers, "fetched_at": time.time()}))
    return tickers, None


def index_info() -> list[dict]:
    """The indexes offered in the Scanners editor, with the saved list's size if there is one."""
    out = []
    for key, (label, *_rest) in INDEXES.items():
        cached = _read_cache(key)
        out.append({"key": key, "label": label, "count": len(cached[0]) if cached else None})
    return out
