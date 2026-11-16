"""Data categories a source can provide. Used to group sources in the UI and describe roles' access."""

CAPABILITIES: dict[str, dict[str, str]] = {
    "realtime_quotes": {"label": "Real-time quotes", "hint": "Live prices, bid/ask, volume"},
    "price_history": {"label": "Price history", "hint": "Historical bars and indicators"},
    "option_chains": {"label": "Option chains", "hint": "Strikes, expiries, IV, Greeks, open interest"},
    "fundamentals": {"label": "Fundamentals", "hint": "Financials, valuation, estimates"},
    "sec_filings": {"label": "SEC filings", "hint": "10-K, 10-Q, 8-K and other EDGAR filings"},
    "insider_trading": {"label": "Insider trading", "hint": "Form 4 insider buys and sells"},
    "dark_pool": {"label": "Dark pool", "hint": "Off-exchange prints and ATS volume"},
    "news": {"label": "News & sentiment", "hint": "News, social media, analyst commentary"},
    "web_search": {"label": "Web search", "hint": "General web search and page reading"},
    "brokerage": {"label": "Brokerage", "hint": "Account, positions, orders"},
    "screening": {"label": "Screening", "hint": "Scanners and saved screens"},
    "other": {"label": "Other", "hint": ""},
}
