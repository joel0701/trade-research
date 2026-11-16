"""Default tools, roles, skills and screeners, inserted on first start.

Edit them in the UI afterwards; this only runs when the tables are empty.
"""
from sqlmodel import Session, select

from app.models import Role, Screener, Skill, Tool

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
    Tool(slug="ibkr", name="Interactive Brokers (MCP)", kind="mcp_server", enabled=False,
         description="IBKR market data, options, screeners and account data via MCP. Set the URL and token, then enable.",
         secret_env="IBKR_MCP_TOKEN",
         config={"transport": "http", "url": "https://REPLACE-WITH-IBKR-MCP-URL/mcp", "blocked_tools": IBKR_BLOCKED}),
    Tool(slug="bigdata", name="Bigdata.com (MCP)", kind="mcp_server", enabled=False,
         description="News, filings, transcripts and company tearsheets via the Bigdata.com MCP server.",
         secret_env="BIGDATA_API_KEY",
         config={"transport": "http", "url": "https://REPLACE-WITH-BIGDATA-MCP-URL/mcp"}),
]

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
     "Price action: trend, support/resistance, momentum, volume, volatility and chart setups.",
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
