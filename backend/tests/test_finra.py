"""FINRA short volume and weekly ATS tools, using samples in FINRA's real formats (checked live)."""
import json
from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.tools import finra

DAILY_HEADER = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"


def daily_file(d: date, short: float, total: float) -> str:
    ds = d.strftime("%Y%m%d")
    return "\n".join([DAILY_HEADER, f"{ds}|AAPL|6815042.794270|25752|12364490.326956|B,Q,N",
                      f"{ds}|CRWV|{short}|1000|{total}|B,Q,N", "12268"])


def venue(week, name, shares, trades, notional):
    return {"weekStartDate": week, "issueSymbolIdentifier": "CRWV", "issueName": "CoreWeave, Inc. Class A Common Stock",
            "tierDescription": "NMS Tier 1", "marketParticipantName": name, "MPID": name[:4],
            "totalWeeklyShareQuantity": shares, "totalWeeklyTradeCount": trades, "totalNotionalSum": notional,
            "summaryTypeCode": "ATS_W_SMBL_FIRM"}


def install(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(finra, "_client", lambda: real(transport=httpx.MockTransport(handler), headers=finra.HEADERS))
    finra._daily_cache.clear()


async def test_short_volume_ratio_and_trend(monkeypatch):
    days = finra._weekdays_back(12)
    holiday = days[3]

    def handler(req: httpx.Request) -> httpx.Response:
        name = req.url.path.rsplit("/", 1)[-1]
        d = date(int(name[9:13]), int(name[13:15]), int(name[15:17]))
        if d == date.today() or d == holiday:
            return httpx.Response(403)  # not posted yet / no file
        recent = d >= days[5]
        return httpx.Response(200, text=daily_file(d, 600 if recent else 400, 1000))

    install(monkeypatch, handler)
    out = json.loads(await finra.finra_short_volume("crwv", days=8))
    assert out["days"] == 8 and all(r["date"] != str(holiday) for r in out["daily"])
    assert out["daily"] == sorted(out["daily"], key=lambda r: r["date"])  # oldest first
    assert out["summary"]["short_pct_last_5d"] > out["summary"]["short_pct_before_that"]
    assert out["daily"][-1]["off_exchange_volume"] == 1000
    assert "No FINRA short-volume data" in await finra.finra_short_volume("ZZZZ", days=3)


def test_parse_daily_skips_trailer_and_bad_rows():
    rows = finra.parse_daily(DAILY_HEADER + "\n20261113|X|1|0|2|Q\nbroken|row\n3\n")
    assert rows == {"X": {"short": 1.0, "short_exempt": 0.0, "total": 2.0, "markets": "Q"}}
    assert finra.parse_daily("<html>error</html>") == {}


async def test_ats_weekly_by_venue(monkeypatch):
    w1, w2 = "2026-10-19", "2026-10-26"
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        kind = next(f["fieldValue"] for f in body["compareFilters"] if f["fieldName"] == "summaryTypeCode")
        seen.append((req.method, kind, body["dateRangeFilters"][0]["fieldName"]))
        if kind == "ATS_W_SMBL_FIRM":
            return httpx.Response(200, json=[venue(w1, "IATS IBKR ATS", 600, 6, 60000), venue(w1, "UBSA UBS ATS", 400, 4, 40000),
                                             venue(w2, "UBSA UBS ATS", 900, 9, 90000), venue(w2, "INCR INTELLIGENT CROSS", 100, 2, 10000)])
        return httpx.Response(200, json=[{"weekStartDate": w1, "totalWeeklyShareQuantity": 3000},
                                         {"weekStartDate": w2, "totalWeeklyShareQuantity": 1000}])

    install(monkeypatch, handler)
    out = json.loads(await finra.finra_ats_weekly("CRWV", weeks=4))
    assert {k for _, k, _ in seen} == {"ATS_W_SMBL_FIRM", "OTC_W_SMBL"} and all(m == "POST" for m, _, _ in seen)
    assert out["company"].startswith("CoreWeave") and [w["week_of"] for w in out["weeks"]] == [w1, w2]
    first, last = out["weeks"]
    assert (first["ats_shares"], first["ats_trades"], first["avg_trade_size"]) == (1000, 10, 100)
    assert first["ats_share_of_off_exchange_pct"] == 25.0 and last["ats_share_of_off_exchange_pct"] == 50.0
    assert first["top_venues"][0] == {"venue": "IATS IBKR ATS", "shares": 600, "pct": 60.0}

    install(monkeypatch, lambda req: httpx.Response(204))
    assert "No FINRA ATS data" in await finra.finra_ats_weekly("ZZZZ")


def test_finra_sources_seeded_and_ready():
    with TestClient(app) as c:
        conn = next(x for x in c.get("/api/connections").json() if x["slug"] == "finra")
        tools = {t["slug"]: t for t in c.get("/api/tools").json() if t["connection_id"] == conn["id"]}
        assert set(tools) == {"finra_short_volume", "finra_ats_weekly"}
        assert all(t["capabilities"] == ["dark_pool"] for t in tools.values())
        status = {s["id"]: s for s in c.get("/api/tools-status").json()}
        assert all(status[t["id"]]["available"] for t in tools.values())  # free, no key
        tech = next(r for r in c.get("/api/roles").json() if r["slug"] == "technical_analyst")
        assert {t["id"] for t in tools.values()} <= set(tech["tool_ids"]) and "dark pool" in tech["description"]
