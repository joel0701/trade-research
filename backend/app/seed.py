"""Default tools, roles, skills and screeners, inserted on first start.

Edit them in the UI afterwards; this only runs when the tables are empty.
"""
import json

from sqlmodel import Session, select

from app.models import Connection, Role, Screener, Skill, Tool

IBKR_BLOCKED = [  # anything that can place orders or change account state stays off by default
    "create_order_instruction", "delete_order_instruction", "create_alert", "update_alert",
    "delete_alert", "set_alert_status", "create_watchlist", "edit_watchlist", "delete_watchlist",
    "create_saved_screener", "update_saved_screener", "delete_saved_screener",
]

TOOLS = [
    Tool(slug="web_search", name="Claude web search", kind="claude_server_tool",
         description="Anthropic-hosted web search (not available on Haiku).",
         config={"type": "web_search_20260209", "name": "web_search", "max_uses": 6}),
    Tool(slug="web_fetch", name="Claude web fetch", kind="claude_server_tool",
         description="Fetch and read a URL found in the conversation (not available on Haiku).",
         config={"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 4}),
    Tool(slug="ddg_search", name="DuckDuckGo search", kind="python", config={"function": "ddg_search"}),
    Tool(slug="price_history", name="Price history (Yahoo)", kind="python", config={"function": "price_history"}),
    Tool(slug="technical_snapshot", name="Technical snapshot (Yahoo)", kind="python",
         config={"function": "technical_snapshot"}),
    Tool(slug="fundamentals", name="Fundamentals (Yahoo)", kind="python", config={"function": "fundamentals"}),
    Tool(slug="options_chain", name="Options chain (Yahoo)", kind="python", config={"function": "options_chain"}),
]

# ── Connections and data sources ──────────────────────────────────────────────
# One connection can serve several sources (IBKR: quotes, options, account, screeners).
# IBKR tool names below are those of IBKR's MCP server; edit allowed_tools if yours differ.
CONNECTIONS = [
    dict(slug="yahoo", name="Yahoo Finance", kind="none",
         description="Free, delayed market data and fundamentals via yfinance. No setup needed."),
    dict(slug="sec_edgar", name="SEC EDGAR", kind="http", secret_env="SEC_USER_AGENT",
         description="Free SEC filings and insider (Form 4) data. The SEC requires SEC_USER_AGENT in .env "
                     "with your app name and contact email."),
    dict(slug="anthropic_tools", name="Anthropic server tools", kind="none",
         description="Web search and fetch run by Anthropic; work with Claude models only."),
    dict(slug="duckduckgo", name="DuckDuckGo", kind="none", description="Free web and news search."),
    dict(slug="ib_gateway", name="Interactive Brokers (IB Gateway)", kind="tws",
         description="IB Gateway or TWS running on this computer, via the TWS API. No key: log in to IB Gateway and "
                     "enable API access. Read-only. Settings: IBKR_HOST, IBKR_PORT (4002 paper, 4001 live), IBKR_CLIENT_ID."),
    dict(slug="ibkr", name="Interactive Brokers", kind="mcp", secret_env="IBKR_MCP_TOKEN",
         description="IBKR's MCP server: real-time quotes, option chains, account data and screeners.",
         config={"transport": "http", "url": "https://REPLACE-WITH-IBKR-MCP-URL/mcp"}),
    dict(slug="finra", name="FINRA (free)", kind="http",
         description="FINRA's free off-exchange data: daily short sale volume (dark pool short ratio) and weekly "
                     "dark pool (ATS) volume by venue. No key needed."),
    dict(slug="massive", name="Massive.com", kind="http", secret_env="MASSIVE_API_KEY",
         description="Massive.com (formerly Polygon.io) REST API: quotes, bars, option chains with Greeks, news "
                     "and trades (off-exchange / dark pool prints). Data access depends on your Massive plan. "
                     "Set MASSIVE_BASE_URL in .env to change the host."),
    dict(slug="bigdata", name="Bigdata.com", kind="mcp", secret_env="BIGDATA_API_KEY",
         description="Bigdata.com MCP server: news, filings, transcripts and company tearsheets.",
         config={"transport": "http", "url": "https://REPLACE-WITH-BIGDATA-MCP-URL/mcp"}),
]

# (slug, name, kind, connection, capabilities, config, description, add to these roles when first created)
DATA_SOURCES = [
    ("sec_filings", "SEC filings (EDGAR)", "python", "sec_edgar", ["sec_filings"], {"function": "sec_filings"}, "",
     ["finance_analyst", "research_agent"]),
    ("sec_filing_text", "Read SEC filing (EDGAR)", "python", "sec_edgar", ["sec_filings"], {"function": "sec_filing_text"}, "",
     ["finance_analyst", "research_agent"]),
    ("insider_trades", "Insider trades (EDGAR Form 4)", "python", "sec_edgar", ["insider_trading"],
     {"function": "insider_trades"}, "", ["finance_analyst", "news_analyst"]),
    ("ibkr_gw_quote", "IBKR quote (IB Gateway)", "python", "ib_gateway", ["realtime_quotes"],
     {"function": "ibkr_quote"}, "", ["technical_analyst", "strategy_optimizer"]),
    ("ibkr_gw_bars", "IBKR price bars (IB Gateway)", "python", "ib_gateway", ["price_history"],
     {"function": "ibkr_bars"}, "", ["technical_analyst"]),
    ("ibkr_gw_options", "IBKR option chain (IB Gateway)", "python", "ib_gateway", ["option_chains"],
     {"function": "ibkr_option_chain"}, "", ["strategy_optimizer"]),
    ("ibkr_gw_account", "IBKR account, read-only (IB Gateway)", "python", "ib_gateway", ["brokerage"],
     {"function": "ibkr_account"}, "", ["strategy_optimizer"]),
    ("ibkr_quotes", "IBKR real-time quotes", "mcp_server", "ibkr", ["realtime_quotes", "price_history"],
     {"allowed_tools": ["get_price_snapshot", "get_price_history", "search_contracts"]},
     "Live quotes and price history from Interactive Brokers.", ["technical_analyst", "strategy_optimizer"]),
    ("ibkr_options", "IBKR option chains", "mcp_server", "ibkr", ["option_chains"],
     {"allowed_tools": ["get_option_parameters", "get_option_data", "get_combo_identifier", "search_contracts"]},
     "Live option chains, IV and Greeks from Interactive Brokers.", ["strategy_optimizer"]),
    ("ibkr_account", "IBKR account (read-only)", "mcp_server", "ibkr", ["brokerage"],
     {"allowed_tools": ["get_account_summary", "get_account_balances", "get_account_positions",
                        "get_account_orders", "get_account_trades", "get_pa_performance_all_periods",
                        "get_pa_allocation"]},
     "Positions, balances, orders and trades. Order placement is always blocked.", []),
    ("ibkr_screeners", "IBKR screeners", "mcp_server", "ibkr", ["screening"],
     {"allowed_tools": ["get_screener_templates", "get_screener_capabilities", "preview_screener",
                        "list_saved_screeners", "get_saved_screener", "run_saved_screener"]},
     "IBKR market scanners and saved screens.", []),
    ("ibkr_research", "IBKR earnings & themes", "mcp_server", "ibkr", ["fundamentals", "news"],
     {"allowed_tools": ["get_earnings_insight", "search_earnings_insights", "get_company_themes",
                        "get_theme_details", "search_investment_topics"]},
     "Earnings insights and investment themes from Interactive Brokers.", ["finance_analyst"]),
    ("finra_short_volume", "FINRA daily short volume", "python", "finra", ["dark_pool"],
     {"function": "finra_short_volume"}, "", ["technical_analyst"]),
    ("finra_ats_weekly", "FINRA weekly dark pool (ATS) volume", "python", "finra", ["dark_pool"],
     {"function": "finra_ats_weekly"}, "", ["technical_analyst"]),
    ("massive_quote", "Massive quote snapshot", "python", "massive", ["realtime_quotes"],
     {"function": "massive_quote"}, "", ["technical_analyst", "strategy_optimizer"]),
    ("massive_bars", "Massive price bars", "python", "massive", ["price_history"],
     {"function": "massive_bars"}, "", ["technical_analyst"]),
    ("massive_option_chain", "Massive option chain", "python", "massive", ["option_chains"],
     {"function": "massive_option_chain"}, "", ["strategy_optimizer"]),
    ("massive_news", "Massive news", "python", "massive", ["news"],
     {"function": "massive_news"}, "", ["news_analyst"]),
    ("massive_offexchange", "Massive off-exchange prints", "python", "massive", ["dark_pool"],
     {"function": "massive_offexchange"}, "", ["technical_analyst"]),
    ("bigdata_research", "Bigdata.com news & filings", "mcp_server", "bigdata", ["news", "sec_filings", "fundamentals"],
     {"blocked_tools": ["bigdata_create_monitor", "bigdata_delete_monitor", "bigdata_edit_monitor",
                        "bigdata_simulate_monitor", "bigdata_create_watchlist", "bigdata_delete_watchlist",
                        "bigdata_edit_watchlist", "bigdata_upload_document"]},
     "News, filings, transcripts and tearsheets (read-only tools).", ["news_analyst"]),
]

# Categories and connections for the built-in sources that already existed.
EXISTING_TAGS = {
    "web_search": ("anthropic_tools", ["web_search", "news"]), "web_fetch": ("anthropic_tools", ["web_search"]),
    "ddg_search": ("duckduckgo", ["web_search", "news"]), "price_history": ("yahoo", ["price_history"]),
    "technical_snapshot": ("yahoo", ["price_history"]), "fundamentals": ("yahoo", ["fundamentals"]),
    "options_chain": ("yahoo", ["option_chains"]),
}


def ensure_data_sources(db: Session) -> None:
    """Create connections and sources that don't exist yet, move older MCP tool settings onto
    connections, and tag categories. Idempotent; never overwrites values you've edited."""
    conns = {c.slug: c for c in db.exec(select(Connection)).all()}
    for c in CONNECTIONS:
        if c["slug"] not in conns:
            conn = Connection(**c)
            db.add(conn)
            db.flush()
            conns[conn.slug] = conn
    tools = {t.slug: t for t in db.exec(select(Tool)).all()}

    # Older databases had one "ibkr" / "bigdata" MCP tool holding the server settings.
    for slug in ("ibkr", "bigdata"):
        old = tools.get(slug)
        if old is None or old.kind != "mcp_server" or old.connection_id is not None:
            continue
        conn = conns[slug]
        server = {k: v for k, v in old.config.items() if k not in ("allowed_tools", "blocked_tools")}
        if server.get("url") and "REPLACE" not in server.get("url", ""):
            conn.config = {**conn.config, **server}  # keep the address you entered
        if old.secret_env:
            conn.secret_env = old.secret_env
        old.connection_id = conn.id
        old.name = f"{conn.name}: all read-only tools"
        old.capabilities = old.capabilities or ["other"]
        db.add_all([conn, old])

    roles = {r.slug: r for r in db.exec(select(Role)).all()}
    for slug, name, kind, conn_slug, caps, config, desc, role_slugs in DATA_SOURCES:
        if slug in tools:
            continue
        tool = Tool(slug=slug, name=name, kind=kind, connection_id=conns[conn_slug].id, capabilities=caps,
                    config=config, description=desc)
        db.add(tool)
        db.flush()
        for rs in role_slugs:  # new sources join sensible roles once, when first created
            role = roles.get(rs)
            if role is not None and tool.id not in role.tool_ids:
                role.tool_ids = [*role.tool_ids, tool.id]
                db.add(role)

    # The IBKR MCP connection was a placeholder (IBKR has no public MCP server with a token).
    # If it was never set up, turn it off so its sources don't sit in roles as "not ready".
    mcp = conns.get("ibkr")
    if mcp is not None and mcp.enabled and "REPLACE" in json.dumps(mcp.config):
        mcp.enabled = False
        mcp.description = ("Unused: IBKR is reached through IB Gateway instead. Only enable this if you run an "
                           "IBKR MCP server and set its address.")
        db.add(mcp)

    # Tell the Lead that the Technical Analyst now covers dark pool flow (only if you haven't edited it).
    tech = roles.get("technical_analyst")
    if tech is not None and tech.description == OLD_TECHNICAL_DESC:
        tech.description = TECHNICAL_DESC
        db.add(tech)

    for slug, (conn_slug, caps) in EXISTING_TAGS.items():
        tool = tools.get(slug)
        if tool is None:
            continue
        if not tool.capabilities:
            tool.capabilities = caps
        if tool.connection_id is None:
            tool.connection_id = conns[conn_slug].id
        db.add(tool)
    db.commit()

OLD_TECHNICAL_DESC = "Price action: trend, support/resistance, momentum, volume, volatility and chart setups."
TECHNICAL_DESC = ("Price action: trend, support/resistance, momentum, volume, volatility and chart setups; "
                  "also dark pool / off-exchange flow (short volume ratio, ATS volume, block prints).")

# (slug, name, model, tools, description, prompt)
ROLES = [
    ("lead", "Lead Analyst", "opus", [],
     "Coordinates the team and writes the final answer.",
     "You are the Lead Analyst on an equity and options research team. Understand what the user is "
     "really asking, consult the specialists you need, weigh their findings against each other, and "
     "give a clear, well-structured answer: conclusion first, then the evidence, risks and what would "
     "change your view. Cite numbers and dates. Use markdown tables for comparisons."),
    ("sector_mapper", "Sector Mapper", "haiku", ["fundamentals", "ddg_search"],
     "Maps a company to its sector, industry, peers, suppliers and customers, and sector-level trends.",
     "You map companies to sectors, industries, peer groups and value chains, and summarise how the sector is behaving."),
    ("finance_analyst", "Finance Analyst", "sonnet", ["fundamentals", "web_search"],
     "Fundamentals: financial statements, valuation multiples, growth, margins, balance sheet, earnings.",
     "You analyse company fundamentals: revenue and earnings trends, margins, cash flow, balance sheet strength, "
     "valuation versus history and peers, and analyst expectations."),
    ("technical_analyst", "Technical Analyst", "sonnet", ["price_history", "technical_snapshot"],
     TECHNICAL_DESC,
     "You analyse price action and indicators: trend, key support and resistance levels, momentum (RSI, MACD), "
     "volume, volatility (ATR) and setups. Always state the timeframe and the levels that would invalidate the view."),
    ("news_analyst", "News Analyst", "sonnet", ["web_search", "ddg_search"],
     "Recent news, catalysts and sentiment from the web, X/Twitter, Reddit and financial media.",
     "You find and summarise recent news, upcoming catalysts and market sentiment (including social media such as "
     "X and Reddit). Give dates and sources for every item and separate facts from opinion."),
    ("strategy_optimizer", "Strategy Optimizer", "opus", ["options_chain", "technical_snapshot"],
     "Designs stock and options trades: strategy choice, strikes, expiries, risk/reward, sizing considerations.",
     "You design trade structures for a given thesis: shares or options strategies (verticals, calendars, covered "
     "calls, cash-secured puts, etc.), specific strikes and expiries from the live chain, max profit/loss, "
     "breakevens and the role implied volatility plays. Always state the risks."),
    ("research_agent", "Research Agent", "sonnet", ["web_search", "web_fetch"],
     "Deep web research on any open question: products, industry reports, filings, management, macro.",
     "You do focused web research on open questions and return sourced findings."),
]

SKILLS = [
    Skill(slug="full_analysis", name="Full stock analysis",
          description="Fundamentals, technicals, news and sector context with a verdict.",
          prompt_template="Give me a full analysis of {ticker}: business and sector context, fundamentals and "
                          "valuation, technical setup, recent news and catalysts, and an overall bull/bear view.",
          params=[{"name": "ticker", "label": "Ticker"}]),
    Skill(slug="earnings_preview", name="Earnings preview",
          description="What to expect into the next earnings report, and the implied move.",
          prompt_template="Prepare an earnings preview for {ticker}: date, consensus expectations, key metrics to "
                          "watch, recent news, the options-implied move, and possible trade setups into the print.",
          params=[{"name": "ticker", "label": "Ticker"}]),
    Skill(slug="options_strategy", name="Options strategy builder",
          description="Suggest options structures for a view on a stock.",
          prompt_template="I am {view} on {ticker} over the next {horizon}. Suggest 2-3 options strategies using "
                          "the live chain, with strikes, expiries, cost, max profit/loss and breakevens.",
          params=[{"name": "ticker", "label": "Ticker"},
                  {"name": "view", "label": "View (bullish / bearish / neutral)", "default": "bullish"},
                  {"name": "horizon", "label": "Horizon", "default": "30 days"}]),
]

MEGACAPS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX", "CRM", "ORCL",
            "ADBE", "INTC", "QCOM", "JPM", "V", "MA", "UNH", "LLY", "XOM", "COST", "WMT", "HD", "PG"]

SCREENERS = [
    Screener(slug="oversold_uptrend", name="Oversold in an uptrend",
             description="Above the 200-day average but short-term oversold (RSI < 40).",
             universe=MEGACAPS, sort_by="rsi_14",
             rules={"all": [{"field": "close", "op": ">", "field2": "sma_200"},
                            {"field": "rsi_14", "op": "<", "value": 40}]}),
    Screener(slug="momentum_breakout", name="Momentum near highs",
             description="Within 3% of the 52-week high, above the 50-day, with elevated volume.",
             universe=MEGACAPS, sort_by="-rel_volume",
             rules={"all": [{"field": "pct_from_52w_high", "op": ">=", "value": -3},
                            {"field": "close", "op": ">", "field2": "sma_50"},
                            {"field": "rel_volume", "op": ">", "value": 1.2}]}),
]


# Prices are USD per million tokens, used for cost estimates in the trace. Claude prices are
# current Anthropic list prices; fill in other vendors' prices from their pricing pages.
# Non-Claude model names change often: check each vendor's model list and edit in Tools.
MODELS = [
    dict(slug="opus", name="Claude Opus 5.5", description="Strongest reasoning at a moderate price.",
         config={"provider": "anthropic", "model_id": "claude-opus-5-5", "effort": "high", "max_tokens": 32000,
                 "fallbacks": "default", "show_thinking": True,
                 "price_input": 4, "price_output": 20, "price_cache_read": 0.2}),
    dict(slug="fable", name="Claude Fable 5.2", description="Most capable model; highest cost.",
         config={"provider": "anthropic", "model_id": "claude-fable-5-2", "effort": "high", "max_tokens": 32000,
                 "fallbacks": "default", "show_thinking": True,
                 "price_input": 8, "price_output": 40, "price_cache_read": 0.2}),
    dict(slug="sonnet", name="Claude Sonnet 5.5", description="Fast, capable workhorse for specialists.",
         config={"provider": "anthropic", "model_id": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 16000,
                 "fallbacks": "default", "price_input": 2, "price_output": 10, "price_cache_read": 0.1}),
    dict(slug="haiku", name="Claude Haiku 5.5", description="Cheapest; simple lookups and summaries.",
         config={"provider": "anthropic", "model_id": "claude-haiku-5-5", "effort": "low", "max_tokens": 8000,
                 "price_input": 0.1, "price_output": 0.5, "price_cache_read": 0.01}),
    dict(slug="gpt", name="OpenAI GPT-5", description="OpenAI's flagship model.",
         config={"provider": "openai", "model_id": "gpt-5", "max_tokens": 32000, "params": {}}),
    dict(slug="gpt_mini", name="OpenAI GPT-5 mini", description="Faster, cheaper OpenAI model.",
         config={"provider": "openai", "model_id": "gpt-5-mini", "max_tokens": 16000, "params": {}}),
    dict(slug="gemini_pro", name="Gemini 2.5 Pro", description="Google's most capable Gemini model.",
         config={"provider": "google", "model_id": "gemini-2.5-pro", "max_tokens": 32000, "params": {}}),
    dict(slug="gemini_flash", name="Gemini 2.5 Flash", description="Fast, low-cost Gemini model.",
         config={"provider": "google", "model_id": "gemini-2.5-flash", "max_tokens": 16000, "params": {}}),
    dict(slug="grok", name="Grok 4", description="xAI's flagship model.",
         config={"provider": "xai", "model_id": "grok-4", "max_tokens": 32000, "params": {}}),
    dict(slug="llama", name="Llama 4 Maverick", description="Meta's Llama via the Llama API.",
         config={"provider": "meta", "model_id": "Llama-4-Maverick-17B-128E-Instruct-FP8", "max_tokens": 8000,
                 "params": {}}),
    dict(slug="mistral_large", name="Mistral Large", description="Mistral's flagship model.",
         config={"provider": "mistral", "model_id": "mistral-large-latest", "max_tokens": 16000, "params": {}}),
    dict(slug="deepseek", name="DeepSeek Chat", description="DeepSeek's general model.",
         config={"provider": "deepseek", "model_id": "deepseek-chat", "max_tokens": 8000, "params": {}}),
]


def ensure_models(db: Session) -> None:
    """Add default models missing from the database, and fill in config keys that older
    databases lack (provider, prices). Never overwrites values you have edited."""
    existing = {t.slug: t for t in db.exec(select(Tool).where(Tool.kind == "model")).all()}
    for m in MODELS:
        tool = existing.get(m["slug"])
        if tool is None:
            db.add(Tool(kind="model", **m))
            continue
        missing = {k: v for k, v in m["config"].items() if k not in tool.config}
        if missing:
            tool.config = {**tool.config, **missing}
            db.add(tool)
    db.commit()


def seed(db: Session) -> None:
    ensure_models(db)
    if db.exec(select(Tool).where(Tool.kind != "model")).first() is not None:
        ensure_data_sources(db)
        return
    db.add_all(TOOLS)
    db.commit()
    by_slug = {t.slug: t for t in db.exec(select(Tool)).all()}
    for slug, name, model, tool_slugs, desc, prompt in ROLES:
        db.add(Role(slug=slug, name=name, description=desc, is_lead=(slug == "lead"),
                    model_tool_id=by_slug[model].id, system_prompt=prompt,
                    tool_ids=[by_slug[s].id for s in tool_slugs]))
    db.add_all(SKILLS)
    db.add_all(SCREENERS)
    db.commit()
    ensure_data_sources(db)
