"""FINRA off-exchange data (free, no key).

1. Daily short sale volume (Reg SHO): for each stock, the off-exchange volume reported to FINRA's
   trade reporting facilities that day and how much of it was short sales. The short share of
   off-exchange volume is the widely followed "dark pool short ratio". Posted by 6 pm ET each day.
   https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt
   Format: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market, then a record-count line.

2. Weekly ATS data (OTC Transparency): shares, trades and dollar value per dark pool (ATS) per
   stock per week, plus non-ATS off-exchange volume. Published with a delay of about 2 weeks for
   Tier 1 stocks (S&P 500, Russell 1000) and about 4 weeks for others.
   POST https://api.finra.org/data/group/otcMarket/name/weeklySummary
"""
import asyncio
import json
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from typing import Any

import httpx

DAILY_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d:%Y%m%d}.txt"
WEEKLY_URL = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"
HEADERS = {"User-Agent": "TradeResearch (FINRA data client)", "Accept": "application/json"}

# Parsed daily files: date -> {symbol: row}. ~0.5 MB each; keep the most recent 60 days in memory.
_daily_cache: "OrderedDict[date, dict[str, dict[str, Any]] | None]" = OrderedDict()
_sem = asyncio.Semaphore(5)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=HEADERS, timeout=30)


def parse_daily(text: str) -> dict[str, dict[str, Any]]:
    """Parse a daily short-volume file by its header names (robust to column changes)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or "|" not in lines[0]:
        return {}
    cols = lines[0].split("|")
    out: dict[str, dict[str, Any]] = {}
    for ln in lines[1:]:
        parts = ln.split("|")
        if len(parts) != len(cols):
            continue  # trailer (record count) or malformed line
        row = dict(zip(cols, parts))
        try:
            out[row["Symbol"].upper()] = {
                "short": float(row.get("ShortVolume") or 0),
                "short_exempt": float(row.get("ShortExemptVolume") or 0),
                "total": float(row.get("TotalVolume") or 0),
                "markets": row.get("Market", ""),
            }
        except ValueError:
            continue
    return out


async def _daily(client: httpx.AsyncClient, d: date) -> dict[str, dict[str, Any]] | None:
    if d in _daily_cache:
        _daily_cache.move_to_end(d)
        return _daily_cache[d]
    async with _sem:
        r = await client.get(DAILY_URL.format(d=d))
    data = parse_daily(r.text) if r.status_code == 200 else None
    if data is not None or d < date.today():  # don't cache "not posted yet" for today
        _daily_cache[d] = data
        while len(_daily_cache) > 60:
            _daily_cache.popitem(last=False)
    return data


def _weekdays_back(n: int, today: date | None = None) -> list[date]:
    d, out = today or date.today(), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


async def finra_short_volume(ticker: str, days: int = 20) -> str:
    """Daily off-exchange short volume ratio for the last N trading days, with trend."""
    t = ticker.strip().upper().replace("-", ".")
    n = max(1, min(days, 60))
    async with _client() as c:
        dates = _weekdays_back(n + 6)  # extra days to cover holidays and today's not-yet-posted file
        files = await asyncio.gather(*(_daily(c, d) for d in dates), return_exceptions=True)
    rows = []
    for d, f in zip(dates, files):
        if isinstance(f, Exception) or not f:
            continue  # holiday, not posted yet, or fetch failed
        r = f.get(t)
        if r and r["total"]:
            rows.append({"date": str(d), "short_volume": round(r["short"]), "short_exempt": round(r["short_exempt"]),
                         "off_exchange_volume": round(r["total"]), "short_pct": round(100 * r["short"] / r["total"], 1)})
        if len(rows) >= n:
            break
    if not rows:
        return f"No FINRA short-volume data found for {t} in the last {n} trading days."
    rows.reverse()  # oldest first

    def avg(xs: list[dict[str, Any]]) -> float | None:
        tot = sum(x["off_exchange_volume"] for x in xs)
        return round(100 * sum(x["short_volume"] for x in xs) / tot, 1) if tot else None

    recent, earlier = rows[-5:], rows[:-5]
    return json.dumps({
        "ticker": t, "days": len(rows),
        "summary": {"short_pct_period": avg(rows), "short_pct_last_5d": avg(recent),
                    "short_pct_before_that": avg(earlier) if earlier else None,
                    "avg_daily_off_exchange_volume": round(sum(x["off_exchange_volume"] for x in rows) / len(rows))},
        "daily": rows,
        "note": "Off-exchange = trades reported to FINRA TRFs (dark pools and broker internalization), not total "
                "market volume. Market makers' hedging is reported as short sales, so ratios near 40-50% are typical; "
                "changes versus the stock's own history matter more than the level.",
    })


async def _weekly(client: httpx.AsyncClient, ticker: str, summary_type: str, start: date) -> list[dict[str, Any]]:
    body = {"limit": 5000,
            "compareFilters": [{"compareType": "equal", "fieldName": "issueSymbolIdentifier", "fieldValue": ticker},
                               {"compareType": "equal", "fieldName": "summaryTypeCode", "fieldValue": summary_type}],
            "dateRangeFilters": [{"fieldName": "weekStartDate", "startDate": str(start), "endDate": str(date.today())}]}
    async with _sem:
        r = await client.post(WEEKLY_URL, json=body)
    if r.status_code == 204 or not r.content.strip():
        return []
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def summarize_ats(venues: list[dict[str, Any]], otc: list[dict[str, Any]], weeks: int) -> dict[str, Any]:
    by_week: dict[str, dict[str, Any]] = defaultdict(lambda: {"ats_shares": 0, "ats_trades": 0, "ats_notional": 0.0, "venues": {}})
    for v in venues:
        w = by_week[v["weekStartDate"]]
        shares, trades = v.get("totalWeeklyShareQuantity") or 0, v.get("totalWeeklyTradeCount") or 0
        w["ats_shares"] += shares
        w["ats_trades"] += trades
        w["ats_notional"] += v.get("totalNotionalSum") or 0
        w["venues"][v.get("marketParticipantName") or v.get("MPID") or "?"] = shares
    otc_by_week = {o["weekStartDate"]: o.get("totalWeeklyShareQuantity") or 0 for o in otc}

    out = []
    for wk in sorted(by_week)[-weeks:]:
        w = by_week[wk]
        non_ats = otc_by_week.get(wk)
        top = sorted(w["venues"].items(), key=lambda kv: kv[1], reverse=True)[:5]
        out.append({
            "week_of": wk, "ats_shares": round(w["ats_shares"]), "ats_trades": w["ats_trades"],
            "ats_notional": round(w["ats_notional"]),
            "avg_trade_size": round(w["ats_shares"] / w["ats_trades"]) if w["ats_trades"] else None,
            "non_ats_otc_shares": round(non_ats) if non_ats is not None else None,
            "ats_share_of_off_exchange_pct": round(100 * w["ats_shares"] / (w["ats_shares"] + non_ats), 1)
            if non_ats else None,
            "top_venues": [{"venue": k, "shares": round(s), "pct": round(100 * s / w["ats_shares"], 1) if w["ats_shares"] else None}
                           for k, s in top],
        })
    return {"weeks": out}


async def finra_ats_weekly(ticker: str, weeks: int = 12) -> str:
    """Weekly dark pool (ATS) volume by venue, and ATS share of off-exchange volume."""
    t = ticker.strip().upper()
    n = max(1, min(weeks, 52))
    start = date.today() - timedelta(weeks=n + 6)  # publication lags 2-4 weeks
    try:
        async with _client() as c:
            venues, otc = await asyncio.gather(_weekly(c, t, "ATS_W_SMBL_FIRM", start),
                                               _weekly(c, t, "OTC_W_SMBL", start))
    except httpx.HTTPError as e:
        return f"FINRA's OTC Transparency API didn't respond as expected: {e}"
    if not venues:
        return f"No FINRA ATS data for {t} in the last {n} weeks (FINRA publishes 2-4 weeks after each week)."
    result = summarize_ats(venues, otc, n)
    first = venues[0]
    return json.dumps({
        "ticker": t, "company": first.get("issueName"), "tier": first.get("tierDescription"),
        **result,
        "note": "ATS = dark pools. Non-ATS OTC = other off-exchange trading (mostly broker/wholesaler internalization). "
                "FINRA publishes about 2 weeks after each week for Tier 1 stocks and about 4 weeks for others.",
    })
