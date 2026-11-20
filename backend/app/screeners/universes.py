"""Stock lists (e.g. the S&P 500): named ticker lists for scanners, skills and questions.

Lists live in the database (Tools → Stock lists). A list's tickers are refreshed only when something
uses it (a scanner run, a skill, or an agent calling the stock_list tool) and it's older than its
schedule allows: daily, weekly or monthly. Refreshing is plain code (download a Wikipedia table and
read the ticker column): no AI model, no tokens. If a refresh fails, the last saved tickers are used.
"""
import io
import json
import logging
import re
from datetime import timedelta

import httpx
from sqlmodel import Session, select

from app.config import CACHE_DIR
from app.models import StockList, now

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "TradeResearch/1.0 (stock research app)"}
REFRESH_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}

# Built-in lists (added on startup unless you deleted them).
DEFAULT_LISTS = [
    dict(slug="sp500", name="S&P 500 (SPY)", symbol="SPY", source="web", refresh="weekly", url="", column="Ticker",
         fallback_url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", fallback_column="Symbol",
         description="The 500 largest US companies, via the SPDR S&P 500 ETF's holdings."),
    dict(slug="nasdaq100", name="Nasdaq-100 (QQQ)", symbol="QQQ", source="web", refresh="weekly", url="", column="Ticker",
         fallback_url="https://en.wikipedia.org/wiki/Nasdaq-100", fallback_column="Ticker",
         description="The 100 largest non-financial Nasdaq companies, via the Invesco QQQ ETF's holdings."),
    dict(slug="dow30", name="Dow 30 (DIA)", symbol="DIA", source="web", refresh="monthly", url="", column="Ticker",
         fallback_url="https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", fallback_column="Symbol",
         description="The Dow Jones Industrial Average, via the SPDR Dow Jones Industrial Average ETF's holdings."),
    dict(slug="schd", name="Dow Jones U.S. Dividend 100 (SCHD)", symbol="SCHD", source="web", refresh="monthly",
         url="", column="Symbol", fallback_url="", fallback_column="Symbol",
         description="100 high-dividend US stocks, via the Schwab U.S. Dividend Equity ETF's holdings."),
]
OLD_NAMES = {"sp500": "S&P 500", "nasdaq100": "Nasdaq-100", "dow30": "Dow 30"}


class UniverseError(Exception):
    pass


def to_yahoo(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


TICKER_COLUMNS = ("Ticker", "Symbol", "Holding Ticker", "Ticker symbol", "Ticker Symbol", "Code", "Stock Symbol")


def _norm_col(c) -> str:
    return " ".join(str(c[-1] if isinstance(c, tuple) else c).split())


def _pick(values) -> list[str]:
    tickers = [to_yahoo(str(v)) for v in values]
    return list(dict.fromkeys(t for t in tickers if t and t not in ("NAN", "-", "--") and len(t) <= 12 and " " not in t))


def parse_constituents(html: str, column: str, min_rows: int = 5) -> list[str]:
    """The `column` values of the largest table on the page that has that column (case-insensitive)."""
    import pandas as pd

    best: list[str] = []
    want = column.strip().lower()
    for table in pd.read_html(io.StringIO(html)):
        cols = [_norm_col(c).lower() for c in table.columns]
        if want in cols and len(table) >= min_rows and len(table) > len(best):
            best = _pick(table.iloc[:, cols.index(want)].tolist())
    if not best:
        raise UniverseError(f"no table with a '{column}' column and at least {min_rows} rows on the page")
    return best


def parse_csv(text: str, column: str, min_rows: int = 5) -> list[str]:
    """The `column` values of a CSV file, skipping any lines before the header (ETF holdings files
    often start with fund details)."""
    import pandas as pd

    want = column.strip().lower()
    lines = text.splitlines()
    for i, line in enumerate(lines[:60]):
        cells = [c.strip().strip('"').lower() for c in line.split(",")]
        if want in cells:
            df = pd.read_csv(io.StringIO("\n".join(lines[i:])), dtype=str, on_bad_lines="skip")
            cols = [str(c).strip().lower() for c in df.columns]
            tickers = _pick(df.iloc[:, cols.index(want)].dropna().tolist())
            if len(tickers) >= min_rows:
                return tickers
    raise UniverseError(f"no '{column}' column with at least {min_rows} rows in the file")


def parse_xlsx(data: bytes, column: str, min_rows: int = 5) -> list[str]:
    """The `column` values of an Excel holdings file, skipping fund-detail rows above the header."""
    import pandas as pd

    want = column.strip().lower()
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str)
    for df in sheets.values():
        for i in range(min(len(df), 60)):
            cells = [str(v).strip().lower() for v in df.iloc[i].tolist()]
            if want in cells:
                tickers = _pick(df.iloc[i + 1:, cells.index(want)].dropna().tolist())
                if len(tickers) >= min_rows:
                    return tickers
    raise UniverseError(f"no '{column}' column with at least {min_rows} rows in the spreadsheet")


def parse_source(content: bytes | str, content_type: str, url: str, column: str) -> list[str]:
    path = url.lower().split("?")[0]
    raw = content if isinstance(content, bytes) else content.encode()
    if "spreadsheet" in content_type or "excel" in content_type or path.endswith((".xlsx", ".xls")):
        return parse_xlsx(raw, column)
    text = raw.decode("utf-8", errors="replace")
    if "csv" in content_type or path.endswith(".csv"):
        return parse_csv(text, column)
    return parse_constituents(text, column)


def fetch_from(url: str, column: str) -> list[str]:
    resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return parse_source(resp.content, resp.headers.get("content-type", ""), url, column or "Symbol")


def fetch_list(sl: StockList) -> tuple[list[str], str]:
    """(tickers, "main" | "backup"): the main source first, then the backup. Raises if both fail."""
    errors = []
    if sl.source in ("web", "wikipedia") and sl.url:
        try:
            return fetch_from(sl.url, sl.column), "main"
        except Exception as e:
            errors.append(f"main source: {type(e).__name__}: {e}")
    if sl.fallback_url:
        try:
            return fetch_from(sl.fallback_url, sl.fallback_column), "backup"
        except Exception as e:
            errors.append(f"backup source: {type(e).__name__}: {e}")
    raise UniverseError("; ".join(errors) or "this list has no source to refresh from")


def is_due(sl: StockList) -> bool:
    """Whether the list should be refreshed before use."""
    if sl.source == "manual" or sl.refresh == "manual":
        return False
    if sl.symbol and not sl.url and sl.tickers and (sl.source_checked_at is None):
        return True  # never looked for its ETF holdings file yet: try once
    if not sl.tickers or sl.refreshed_at is None:
        return True
    return now() - sl.refreshed_at >= timedelta(days=REFRESH_DAYS.get(sl.refresh, 7))


def _run_in_thread(symbol: str) -> dict:
    """Run the async ETF search to completion on its own thread (callers may already be inside an
    event loop, e.g. a scanner run), with its own database session."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from app.db import engine
    from app.screeners.discover import find_etf_source

    def go() -> dict:
        with Session(engine) as db2:
            return asyncio.run(find_etf_source(db2, symbol))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(go).result(timeout=120)


def _auto_find(db: Session, sl: StockList) -> None:
    """A list with an ETF symbol but no main source yet looks for the ETF's holdings file once (then at
    most monthly), so the official file is used without anyone having to click Find source."""
    if sl.url or not sl.symbol or sl.source == "manual":
        return
    if sl.source_checked_at and now() - sl.source_checked_at < timedelta(days=30):
        return
    sl.source_checked_at = now()
    db.add(sl)
    db.commit()
    try:
        found = _run_in_thread(sl.symbol)
    except Exception as e:  # no model available, or nothing found
        log.info("No ETF holdings source found for %s: %s", sl.symbol, e)
        return
    sl.url, sl.column, sl.source = found["url"], found["column"], "web"
    sl.source_note = f"ETF holdings found by {found['found_by']} on {now().date().isoformat()}"
    db.add(sl)
    db.commit()


def refresh(db: Session, sl: StockList) -> StockList:
    """Download the list now: the main source (ETF holdings), else the backup (Wikipedia). Raises if
    both fail; the saved tickers are kept and the error recorded."""
    _auto_find(db, sl)
    try:
        tickers, used = fetch_list(sl)
    except Exception as e:
        sl.last_error = (f"{e}"[:300] + " (a source may have moved: use Find source)")
        db.add(sl)
        db.commit()
        raise UniverseError(f"Couldn't refresh {sl.name}: {e}") from e
    sl.tickers, sl.refreshed_at, sl.last_source = tickers, now(), used
    sl.last_error = ("The main source failed, so the backup was used. Find source may fix the main source."
                     if used == "backup" and sl.url else None)
    db.add(sl)
    db.commit()
    db.refresh(sl)
    log.info("Refreshed stock list %s from its %s source: %d tickers", sl.slug, used, len(tickers))
    return sl


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_list(db: Session, name: str) -> StockList | None:
    """A list by slug or name, ignoring case and punctuation ("S&P 500" = "sp500" = "S&P500")."""
    k = _key(name)
    for sl in db.exec(select(StockList).where(StockList.enabled == True)).all():  # noqa: E712
        names = {_key(sl.slug), _key(sl.name), _key(re.sub(r"\(.*?\)", "", sl.name)), _key(sl.symbol)}
        if k and k in names:
            return sl
    return None


def get_list(db: Session, name: str) -> tuple[StockList, str | None]:
    """The list, refreshed first if it's due. Returns (list, note); note is set when a refresh failed
    and the saved tickers are used instead."""
    sl = find_list(db, name)
    if sl is None:
        raise UniverseError(f"No stock list called {name!r}. Add it in Tools → Stock lists.")
    if not is_due(sl):
        return sl, None
    try:
        return refresh(db, sl), None
    except UniverseError as e:
        if sl.tickers:
            day = sl.refreshed_at.strftime("%Y-%m-%d") if sl.refreshed_at else "earlier"
            return sl, f"Couldn't refresh the {sl.name} list; using the one saved on {day}."
        raise UniverseError(f"{e}. Check the internet connection, or use a custom ticker list.") from e


def _upgrade(db: Session, sl: StockList, spec: dict) -> None:
    """Built-in lists from before ETF sources: the ETF becomes the main source (found on first use) and
    the Wikipedia table the backup. Tickers are kept; names you changed are left alone."""
    changed = False
    if sl.name == OLD_NAMES.get(sl.slug):
        sl.name, changed = spec["name"], True
    if sl.symbol in ("", "SPX", "NDX", "DJI"):
        sl.symbol, changed = spec["symbol"], True
    if sl.url and sl.url == spec["fallback_url"] and not sl.fallback_url:
        sl.fallback_url, sl.fallback_column = sl.url, sl.column
        sl.url, sl.column, changed = "", spec["column"], True
    if changed:
        db.add(sl)


def ensure_stock_lists(db: Session, dismissed: set[str]) -> None:
    """Add the built-in lists if missing (not ones you deleted), and upgrade older built-in lists. Lists saved by the earlier file cache
    (data/cache/index_<slug>.json) are carried over so they don't need downloading again."""
    existing = {sl.slug: sl for sl in db.exec(select(StockList)).all()}
    for spec in DEFAULT_LISTS:
        if spec["slug"] in existing:
            _upgrade(db, existing[spec["slug"]], spec)
            continue
        if spec["slug"] in dismissed:
            continue
        sl = StockList(**spec)
        try:
            data = json.loads((CACHE_DIR / f"index_{spec['slug']}.json").read_text())
            from datetime import datetime, timezone
            sl.tickers = data["tickers"]
            sl.refreshed_at = datetime.fromtimestamp(float(data["fetched_at"]), tz=timezone.utc)
        except (OSError, ValueError, KeyError):
            pass
        db.add(sl)
    db.commit()


def list_info(db: Session) -> list[dict]:
    """Lists offered in the Scanners editor."""
    return [{"key": sl.slug, "label": sl.name, "count": len(sl.tickers) or None}
            for sl in db.exec(select(StockList).where(StockList.enabled == True).order_by(StockList.id)).all()]  # noqa: E712
