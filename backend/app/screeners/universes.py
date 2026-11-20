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
    dict(slug="sp500", name="S&P 500", source="wikipedia", refresh="weekly", column="Symbol",
         url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
         description="The 500 largest US companies (S&P Dow Jones Indices)."),
    dict(slug="nasdaq100", name="Nasdaq-100", source="wikipedia", refresh="weekly", column="Ticker",
         url="https://en.wikipedia.org/wiki/Nasdaq-100",
         description="The 100 largest non-financial companies on Nasdaq."),
    dict(slug="dow30", name="Dow 30", source="wikipedia", refresh="monthly", column="Symbol",
         url="https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
         description="The Dow Jones Industrial Average: 30 large US companies."),
]


class UniverseError(Exception):
    pass


def to_yahoo(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


def parse_constituents(html: str, column: str, min_rows: int = 5) -> list[str]:
    """The `column` values of the largest table on the page that has that column."""
    import pandas as pd

    best: list[str] = []
    for table in pd.read_html(io.StringIO(html)):
        cols = [str(c[-1] if isinstance(c, tuple) else c).strip() for c in table.columns]
        if column in cols and len(table) >= min_rows and len(table) > len(best):
            values = [to_yahoo(str(v)) for v in table.iloc[:, cols.index(column)].tolist()]
            best = list(dict.fromkeys(v for v in values if v and v != "NAN"))
    if not best:
        raise UniverseError(f"no table with a '{column}' column and at least {min_rows} rows on the page")
    return best


def fetch_list(sl: StockList) -> list[str]:
    if sl.source != "wikipedia" or not sl.url:
        raise UniverseError("this list has no source to refresh from")
    resp = httpx.get(sl.url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return parse_constituents(resp.text, sl.column or "Symbol")


def is_due(sl: StockList) -> bool:
    """Whether the list should be refreshed before use."""
    if sl.source == "manual" or sl.refresh == "manual":
        return False
    if not sl.tickers or sl.refreshed_at is None:
        return True
    return now() - sl.refreshed_at >= timedelta(days=REFRESH_DAYS.get(sl.refresh, 7))


def refresh(db: Session, sl: StockList) -> StockList:
    """Download the list now (raises if it fails; the saved tickers are kept and the error recorded)."""
    try:
        tickers = fetch_list(sl)
    except Exception as e:
        sl.last_error = f"{type(e).__name__}: {e}"[:300]
        db.add(sl)
        db.commit()
        raise UniverseError(f"Couldn't refresh {sl.name}: {e}") from e
    sl.tickers, sl.refreshed_at, sl.last_error = tickers, now(), None
    db.add(sl)
    db.commit()
    db.refresh(sl)
    log.info("Refreshed stock list %s: %d tickers", sl.slug, len(tickers))
    return sl


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_list(db: Session, name: str) -> StockList | None:
    """A list by slug or name, ignoring case and punctuation ("S&P 500" = "sp500" = "S&P500")."""
    k = _key(name)
    for sl in db.exec(select(StockList).where(StockList.enabled == True)).all():  # noqa: E712
        if k in (_key(sl.slug), _key(sl.name)):
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


def ensure_stock_lists(db: Session, dismissed: set[str]) -> None:
    """Add the built-in lists if missing (not ones you deleted). Lists saved by the earlier file cache
    (data/cache/index_<slug>.json) are carried over so they don't need downloading again."""
    existing = {sl.slug for sl in db.exec(select(StockList)).all()}
    for spec in DEFAULT_LISTS:
        if spec["slug"] in existing or spec["slug"] in dismissed:
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
