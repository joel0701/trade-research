"""SEC EDGAR: company filings and insider (Form 4) transactions. Free, no API key.

The SEC requires every request to identify the caller with a User-Agent containing a name and
contact email, e.g. "TradeResearch you@example.com". Set SEC_USER_AGENT in .env. The SEC also
limits clients to 10 requests per second; these tools stay well under that.
Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""
import asyncio
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

TX_CODES = {"P": "Open-market buy", "S": "Open-market sale", "A": "Grant/award", "M": "Option exercise",
            "F": "Tax withholding", "G": "Gift", "D": "Disposed to issuer", "C": "Conversion",
            "X": "Option exercise", "J": "Other"}

_cik_cache: dict[str, tuple[int, str]] = {}
_sem = asyncio.Semaphore(4)  # stay well under the SEC's 10 requests/second


class EdgarError(Exception):
    pass


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def configured_email() -> str | None:
    """The contact email saved in Settings (on the SEC EDGAR connection), if any."""
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Connection

    with Session(engine) as db:
        conn = db.exec(select(Connection).where(Connection.slug == "sec_edgar")).first()
    email = (conn.config.get("contact_email") or "").strip() if conn else ""
    return email if EMAIL.match(email) else None


def user_agent() -> str | None:
    """SEC_USER_AGENT from .env if set, otherwise built from the email saved in Settings."""
    ua = os.getenv("SEC_USER_AGENT", "").strip()
    if ua and "@" in ua:
        return ua
    email = configured_email()
    return f"TradeResearch {email}" if email else None


def _headers() -> dict[str, str]:
    ua = user_agent()
    if not ua:
        raise EdgarError("SEC EDGAR needs your contact email (an SEC requirement). Add it in Settings under "
                         "SEC EDGAR, or set SEC_USER_AGENT in .env.")
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    async with _sem:
        r = await client.get(url, headers=_headers(), timeout=30, follow_redirects=True)
    if r.status_code == 404:
        raise EdgarError(f"Not found on EDGAR: {url}")
    r.raise_for_status()
    return r


async def lookup_cik(client: httpx.AsyncClient, ticker: str) -> tuple[int, str]:
    t = ticker.strip().upper().replace(".", "-")
    if not _cik_cache:
        data = (await _get(client, TICKERS_URL)).json()
        for row in data.values():
            _cik_cache[str(row["ticker"]).upper()] = (int(row["cik_str"]), row["title"])
    if t not in _cik_cache:
        raise EdgarError(f"{ticker} isn't in the SEC's ticker list (it may not file with the SEC).")
    return _cik_cache[t]


def recent_filings(submissions: dict[str, Any], cik: int, forms: set[str] | None, limit: int) -> list[dict[str, Any]]:
    """Rows from the submissions JSON 'recent' block, newest first, optionally filtered by form type."""
    rec = submissions.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if forms and form.upper() not in forms:
            continue
        acc = rec["accessionNumber"][i]
        doc = rec["primaryDocument"][i]
        out.append({
            "form": form, "filed": rec["filingDate"][i], "period": rec.get("reportDate", [""] * (i + 1))[i] or None,
            "description": (rec.get("primaryDocDescription", [""] * (i + 1))[i] or "").strip() or None,
            "accession": acc, "document": doc,
            "url": ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc),
        })
        if len(out) >= limit:
            break
    return out


async def sec_filings(ticker: str, forms: str = "10-K,10-Q,8-K", limit: int = 10) -> str:
    wanted = {f.strip().upper() for f in forms.split(",") if f.strip()} or None
    async with httpx.AsyncClient() as client:
        try:
            cik, name = await lookup_cik(client, ticker)
            subs = (await _get(client, SUBMISSIONS_URL.format(cik=cik))).json()
        except EdgarError as e:
            return str(e)
    rows = recent_filings(subs, cik, wanted, max(1, min(limit, 50)))
    return json.dumps({"company": name, "cik": cik, "filings": rows})


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|ix:header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


async def sec_filing_text(url: str, find: str = "", max_chars: int = 20000) -> str:
    """Read a filing document as plain text, optionally starting at a phrase like 'Risk Factors'."""
    if not url.startswith("https://www.sec.gov/"):
        return "Only https://www.sec.gov/ document URLs (from sec_filings) can be read."
    async with httpx.AsyncClient() as client:
        try:
            text = html_to_text((await _get(client, url)).text)
        except EdgarError as e:
            return str(e)
    start = 0
    if find:
        hits = [m.start() for m in re.finditer(re.escape(find), text, re.I)]
        # The first hit is usually the table of contents; prefer a later one if there is one.
        start = hits[1] if len(hits) > 1 else (hits[0] if hits else 0)
    chunk = text[start:start + max(1000, min(max_chars, 60000))]
    note = "" if not find or start else f"(phrase '{find}' not found; showing the start)\n"
    return f"{note}{chunk}" + ("\n…[truncated]" if start + len(chunk) < len(text) else "")


def _v(el: ET.Element | None, path: str) -> str | None:
    """Value of a Form 4 field, which is wrapped in <value> in most places."""
    if el is None:
        return None
    node = el.find(path)
    if node is None:
        return None
    inner = node.find("value")
    return ((inner.text if inner is not None else node.text) or "").strip() or None


def parse_form4(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    owner = root.find("reportingOwner")
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = []
    if rel is not None:
        if _v(rel, "isDirector") in ("1", "true"):
            roles.append("Director")
        if _v(rel, "isOfficer") in ("1", "true"):
            roles.append(_v(rel, "officerTitle") or "Officer")
        if _v(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% owner")
    who = _v(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    rows = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _v(tx, "transactionCoding/transactionCode")
        shares = float(_v(tx, "transactionAmounts/transactionShares") or 0)
        price = float(_v(tx, "transactionAmounts/transactionPricePerShare") or 0)
        rows.append({
            "date": _v(tx, "transactionDate"), "insider": who, "role": ", ".join(roles) or None,
            "code": code, "type": TX_CODES.get(code or "", code),
            "direction": "buy" if _v(tx, "transactionAmounts/transactionAcquiredDisposedCode") == "A" else "sell",
            "shares": shares, "price": price or None, "value": round(shares * price, 2) if price else None,
            "owned_after": float(_v(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction") or 0) or None,
        })
    return rows


async def insider_trades(ticker: str, filings: int = 15) -> str:
    """Recent Form 4 insider transactions with a buy/sell summary (open-market trades only in totals)."""
    async with httpx.AsyncClient() as client:
        try:
            cik, name = await lookup_cik(client, ticker)
            subs = (await _get(client, SUBMISSIONS_URL.format(cik=cik))).json()
            form4s = recent_filings(subs, cik, {"4"}, max(1, min(filings, 40)))

            async def one(f: dict[str, Any]) -> list[dict[str, Any]]:
                # primaryDocument is the styled view (xslF345X05/x.xml); the raw XML has the same name.
                raw_doc = f["document"].split("/")[-1]
                url = ARCHIVE_URL.format(cik=cik, acc=f["accession"].replace("-", ""), doc=raw_doc)
                try:
                    rows = parse_form4((await _get(client, url)).text)
                except (EdgarError, ET.ParseError, httpx.HTTPError):
                    return []
                return [{**r, "filed": f["filed"], "url": f["url"]} for r in rows]

            batches = await asyncio.gather(*(one(f) for f in form4s))
        except EdgarError as e:
            return str(e)
    rows = [r for b in batches for r in b]
    buys = [r for r in rows if r["code"] == "P"]
    sells = [r for r in rows if r["code"] == "S"]
    summary = {
        "open_market_buys": len(buys), "buy_value": round(sum(r["value"] or 0 for r in buys), 2),
        "open_market_sales": len(sells), "sale_value": round(sum(r["value"] or 0 for r in sells), 2),
        "form4_filings_checked": len(form4s),
    }
    return json.dumps({"company": name, "cik": cik, "summary": summary, "transactions": rows[:60]})
