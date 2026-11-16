"""Connections shared by several data sources, availability, trading block, EDGAR, and migration."""
import json
from types import SimpleNamespace as NS

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.models import Connection, Role, Tool
from app.seed import ensure_data_sources
from app.tools import sec_edgar
from app.tools.registry import Toolbox, is_trading_tool


class FakeMCP:
    def __init__(self, names):
        self.names = names
        self.calls = []

    async def list_tools(self):
        return NS(tools=[NS(name=n, description=n, inputSchema={"type": "object"}) for n in self.names])

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return NS(content=[NS(type="text", text=f"{name} ok")], isError=False)


async def test_sources_share_one_connection(monkeypatch):
    monkeypatch.setenv("IBKR_MCP_TOKEN", "t")
    conn = Connection(id=1, slug="ibkr", name="IBKR", kind="mcp", secret_env="IBKR_MCP_TOKEN",
                      config={"transport": "http", "url": "https://example.test/mcp"})
    quotes = Tool(id=10, slug="q", name="quotes", kind="mcp_server", connection_id=1,
                  config={"allowed_tools": ["get_price_snapshot"]})
    account = Tool(id=11, slug="a", name="account", kind="mcp_server", connection_id=1,
                   config={"allowed_tools": ["get_account_positions", "create_order_instruction"]})
    server = FakeMCP(["get_price_snapshot", "get_account_positions", "create_order_instruction", "get_option_data"])
    opened = []

    async def fake_open(self, cfg, secret_env):
        opened.append((cfg["url"], secret_env))
        return server

    monkeypatch.setattr(Toolbox, "open_mcp", fake_open)
    async with Toolbox([quotes, account], {1: conn}) as tb:
        names = sorted(d["name"] for d in tb.definitions)
        assert opened == [("https://example.test/mcp", "IBKR_MCP_TOKEN")]  # one session for both sources
        assert names == ["ibkr__get_account_positions", "ibkr__get_price_snapshot"]  # order tool blocked
        assert await tb.execute("ibkr__get_price_snapshot", {"symbol": "EQT"}) == ("get_price_snapshot ok", False)


async def test_unavailable_sources_are_skipped(monkeypatch):
    monkeypatch.delenv("IBKR_MCP_TOKEN", raising=False)
    conn = Connection(id=1, slug="ibkr", name="IBKR", kind="mcp", secret_env="IBKR_MCP_TOKEN",
                      config={"url": "https://example.test/mcp"})
    tool = Tool(id=10, slug="q", name="IBKR quotes", kind="mcp_server", connection_id=1)
    async with Toolbox([tool], {1: conn}) as tb:
        assert tb.definitions == [] and tb.skipped == [("IBKR quotes", "set IBKR_MCP_TOKEN in .env")]


def test_trading_tools_detected():
    for name in ["create_order_instruction", "delete_order_instruction", "place_order", "cancel_order",
                 "modify_order", "submit_trade", "order_create", "transfer_funds"]:
        assert is_trading_tool(name), name
    for name in ["get_account_orders", "get_account_trades", "get_order_instructions", "get_price_snapshot",
                 "get_option_data", "run_saved_screener"]:
        assert not is_trading_tool(name), name


def test_status_endpoint_explains_missing_setup(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with TestClient(app) as c:
        tools = {t["slug"]: t["id"] for t in c.get("/api/tools").json()}
        status = {s["id"]: s for s in c.get("/api/tools-status").json()}
        assert status[tools["sec_filings"]] == {"id": tools["sec_filings"], "available": False,
                                                "reason": "set SEC_USER_AGENT in .env"}
        assert not status[tools["bigdata_research"]]["available"]  # Bigdata.com not set up
        assert status[tools["fundamentals"]]["available"]  # Yahoo needs no setup
        monkeypatch.setenv("SEC_USER_AGENT", "TradeResearch test@example.com")
        status = {s["id"]: s for s in c.get("/api/tools-status").json()}
        assert status[tools["sec_filings"]]["available"]
        caps = [x["slug"] for x in c.get("/api/capabilities").json()]
        assert {"dark_pool", "insider_trading", "sec_filings", "realtime_quotes", "brokerage"} <= set(caps)


FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Rice Toby Z.</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer>
      <officerTitle>President and CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-03-02</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>51.25</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>2500000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-03-05</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>4000</value></transactionShares>
        <transactionPricePerShare><value>53</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>2496000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""

SUBMISSIONS = {"filings": {"recent": {
    "form": ["4", "10-Q", "8-K", "4"],
    "filingDate": ["2026-03-06", "2026-02-12", "2026-01-20", "2026-01-05"],
    "reportDate": ["2026-03-05", "2025-12-31", "", "2026-01-02"],
    "accessionNumber": ["0001-26-000004", "0001-26-000003", "0001-26-000002", "0001-26-000001"],
    "primaryDocument": ["xslF345X05/form4.xml", "eqt-20251231.htm", "8k.htm", "xslF345X05/form4.xml"],
    "primaryDocDescription": ["4", "10-Q", "8-K", "4"],
}}}


def edgar_client(monkeypatch, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("user-agent")))
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 33213, "ticker": "EQT", "title": "EQT Corp"}})
        if "submissions/CIK0000033213.json" in url:
            return httpx.Response(200, json=SUBMISSIONS)
        if url.endswith("/form4.xml"):
            return httpx.Response(200, text=FORM4)
        if url.endswith("eqt-20251231.htm"):
            return httpx.Response(200, text="<html><body><p>Contents: Risk Factors</p><h2>Risk Factors</h2>"
                                             "<p>Gas prices &amp; storage levels matter.</p></body></html>")
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(sec_edgar.httpx, "AsyncClient", lambda: real(transport=httpx.MockTransport(handler)))
    sec_edgar._cik_cache.clear()


async def test_edgar_filings_text_and_insiders(monkeypatch):
    seen = []
    edgar_client(monkeypatch, seen)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert "SEC_USER_AGENT" in await sec_edgar.sec_filings("EQT")  # required by the SEC

    monkeypatch.setenv("SEC_USER_AGENT", "TradeResearch test@example.com")
    out = json.loads(await sec_edgar.sec_filings("eqt", forms="10-Q,8-K"))
    assert out["company"] == "EQT Corp" and [f["form"] for f in out["filings"]] == ["10-Q", "8-K"]
    assert out["filings"][0]["url"] == "https://www.sec.gov/Archives/edgar/data/33213/000126000003/eqt-20251231.htm"
    assert all(ua == "TradeResearch test@example.com" for _, ua in seen)

    text = await sec_edgar.sec_filing_text(out["filings"][0]["url"], find="Risk Factors")
    assert text.startswith("Risk Factors") and "Gas prices & storage levels matter." in text
    assert "Only https://www.sec.gov/" in await sec_edgar.sec_filing_text("https://evil.test/x")

    ins = json.loads(await sec_edgar.insider_trades("EQT"))
    assert ins["summary"] == {"open_market_buys": 2, "buy_value": 1025000.0, "open_market_sales": 2,
                              "sale_value": 424000.0, "form4_filings_checked": 2}
    first = ins["transactions"][0]
    assert first["insider"] == "Rice Toby Z." and first["role"] == "Director, President and CEO"
    assert first["type"] == "Open-market buy" and first["value"] == 512500.0
    # The raw XML (not the styled xsl view) was fetched.
    assert any(u.endswith("/000126000004/form4.xml") for u, _ in seen)
    assert "isn't in the SEC's ticker list" in await sec_edgar.insider_trades("ZZZZ")


def test_older_database_is_migrated(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tool(slug="ibkr", name="Interactive Brokers (MCP)", kind="mcp_server", secret_env="MY_IBKR",
                    config={"transport": "http", "url": "https://my-ibkr.example/mcp",
                            "blocked_tools": ["create_order_instruction"]}))
        db.add(Role(slug="finance_analyst", name="Finance Analyst", tool_ids=[]))
        db.commit()
        ensure_data_sources(db)
        ensure_data_sources(db)  # idempotent
        conn = db.exec(select(Connection).where(Connection.slug == "ibkr")).one()
        assert conn.config["url"] == "https://my-ibkr.example/mcp" and conn.secret_env == "MY_IBKR"
        tools = {t.slug: t for t in db.exec(select(Tool)).all()}
        assert tools["ibkr"].connection_id == conn.id
        assert tools["ibkr"].config["blocked_tools"] == ["create_order_instruction"]  # kept
        assert "ibkr_quotes" not in tools  # old MCP sources are no longer created
        role = db.exec(select(Role)).one()
        assert {tools["sec_filings"].id, tools["insider_trades"].id} <= set(role.tool_ids)
        assert len(db.exec(select(Connection)).all()) == len({c.slug for c in db.exec(select(Connection)).all()})


def test_vendor_first_renames_respect_edits(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'names.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tool(slug="price_history", name="Price history (Yahoo)", kind="python", config={"function": "price_history"}))
        db.add(Tool(slug="fundamentals", name="My fundamentals", kind="python", config={"function": "fundamentals"}))
        db.commit()
        ensure_data_sources(db)
        names = {t.slug: t.name for t in db.exec(select(Tool)).all()}
        assert names["price_history"] == "Yahoo price history"
        assert names["fundamentals"] == "My fundamentals"  # edited by the user: left alone
        assert names["sec_filings"] == "SEC EDGAR filings"


def test_unconfigured_ibkr_mcp_placeholder_is_removed(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        conn = Connection(slug="ibkr", name="Interactive Brokers", kind="mcp", secret_env="IBKR_MCP_TOKEN",
                          config={"transport": "http", "url": "https://REPLACE-WITH-IBKR-MCP-URL/mcp"})
        db.add(conn)
        db.flush()
        q = Tool(slug="ibkr_quotes", name="IBKR real-time quotes", kind="mcp_server", connection_id=conn.id)
        legacy = Tool(slug="ibkr", name="Interactive Brokers (MCP)", kind="mcp_server",
                      config={"url": "https://REPLACE-WITH-IBKR-MCP-URL/mcp"})
        db.add_all([q, legacy])
        db.flush()
        db.add(Role(slug="technical_analyst", name="Technical Analyst", tool_ids=[q.id, legacy.id]))
        db.commit()
        ensure_data_sources(db)
        slugs = {t.slug for t in db.exec(select(Tool)).all()}
        assert not {"ibkr_quotes", "ibkr"} & slugs and "ibkr_gw_scanner" in slugs
        assert db.exec(select(Connection).where(Connection.slug == "ibkr")).first() is None
        role = db.exec(select(Role)).one()
        ids = {t.id: t.slug for t in db.exec(select(Tool)).all()}
        assert all(i in ids for i in role.tool_ids)  # no dangling references
        assert {"ibkr_gw_quote", "ibkr_gw_scanner"} <= {ids[i] for i in role.tool_ids}
