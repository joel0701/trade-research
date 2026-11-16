# Trade Research

A multi-agent research assistant for stocks and options. You chat with a **Lead Analyst**,
which consults a team of specialists (Sector Mapper, Finance Analyst, Technical Analyst,
News Analyst, Strategy Optimizer, Research Agent), each with its own Claude model, prompt and tools.
Every chat session is saved and can be reopened and continued. **Skills** (pre-built analyses) and
**Screeners** (rule-based scans) each start a new session with their results.

Roles, tools, skills and screeners are all data, editable in the app; no code change is needed to
add a role or connect a tool.

## Architecture

```
React UI (Vite)  ──/api──►  FastAPI
                             ├─ Orchestrator: Lead Analyst agent loop
                             │    └─ consult_<role> tools → specialist agent loops (run in parallel)
                             ├─ Tool registry: Claude models, Python functions,
                             │    Claude server tools (web search/fetch), MCP servers (IBKR, Bigdata.com…)
                             ├─ Screener engine: deterministic rules over computed indicators
                             └─ SQLite (SQLModel): sessions, messages, runs, tools, roles, skills, screeners
```

- **No agent framework.** `backend/app/agents/loop.py` is a small loop over the Anthropic Messages API.
  The Lead and the specialists share it; a specialist is just a tool the Lead can call.
- **Sessions keep the full API history**, including hidden tool calls and thinking blocks, so a
  conversation can be continued exactly. The UI shows only the visible text.
- **Runs** record every Lead and specialist call (input, output, tool calls, token usage):
  `GET /api/sessions/{id}/runs`.
- **Screeners never use the LLM to scan.** They compute indicators in pandas and apply JSON rules;
  only the matches go to the Lead.
- **Safety:** the IBKR MCP tool ships disabled, with every order, alert and watchlist-changing tool
  blocked. Secrets live in `.env`; tool configs only name the environment variable.

## Run locally

Requires Python 3.11+ with [uv](https://docs.astral.sh/uv/), and Node 20+.

```bash
cp .env.example .env        # add your ANTHROPIC_API_KEY
```

Backend (http://localhost:8321, API docs at /docs):

```bash
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8321
```

Port 8321 avoids clashing with other tools that commonly use 8000. To use a different port,
start uvicorn with `--port N` and the frontend with `BACKEND_PORT=N npm run dev`.

Frontend (http://localhost:5173):

```bash
cd frontend && npm install && npm run dev
```

The database is created at `data/trade_research.db` and seeded with default roles, tools, skills
and screeners on first start. Delete the file to reset.

Tests (no API calls; Claude is faked):

```bash
cd backend && uv run pytest
```

## Configuring

| Screen | What it controls |
|---|---|
| **Tools** | Models (model id, effort, max tokens, refusal fallbacks), built-in Python functions, Claude web search/fetch, MCP servers |
| **Roles** | Each analyst's model, system prompt, allowed tools, iteration limit; which role is the Lead |
| **Skills** | Prompt templates with parameters, e.g. `{ticker}` |
| **Screeners** | Ticker universe, rules, sort, follow-up prompt |

Default models: Lead and Strategy Optimizer on Claude Opus 5.5; most specialists on Sonnet 5.5;
Sector Mapper on Haiku 5.5. Opus, Sonnet and Fable use server-side refusal fallbacks
(`"fallbacks": "default"` in the model config); remove it to opt out.

### LLM vendors

Keys for each vendor go in `.env`, organized by vendor in `.env.example`: Anthropic, OpenAI,
Google (Gemini), Meta (Llama API), xAI (Grok), Mistral and DeepSeek. **Only models whose vendor
key is set appear in the model picker**; Roles and Tools flag models without a key.

Claude models use the Anthropic SDK. All other vendors are called through their
OpenAI-compatible endpoints with the OpenAI SDK (`backend/app/llm/`). Sessions are stored in one
format, so a session can switch vendors between questions. On non-Claude models:

- Claude-only features are skipped: web search/fetch server tools, thinking display, refusal fallbacks.
- Attached PDFs are sent as extracted text (Claude reads the PDF itself, including charts).

Default model names for other vendors change often: check them under Tools and fill in
`price_input` / `price_output` (USD per million tokens) to get cost estimates.

### Data sources and connections

**Tools → Connections** holds how to reach each provider (endpoint and the name of the `.env`
variable with its key). **Tools → Data sources** lists what each connection provides, grouped by
category: real-time quotes, price history, option chains, fundamentals, SEC filings, insider trading,
dark pool, news, web search, brokerage, screening. One connection can serve several sources: the
IBKR connection backs *IBKR real-time quotes*, *option chains*, *account (read-only)*, *screeners*
and *earnings & themes*, and a role using several of them shares one MCP session.

Built in: Yahoo Finance (free, delayed), DuckDuckGo, Claude web search/fetch, and **SEC EDGAR**
(free): recent filings, reading a filing's text, and Form 4 insider trades. EDGAR needs
`SEC_USER_AGENT` in `.env` with an app name and contact email (an SEC requirement).

**FINRA** (free, no key): daily off-exchange short volume (the "dark pool short ratio", posted by
6 pm ET) and weekly dark pool (ATS) volume by venue with ATS share of off-exchange volume (published
2-4 weeks after each week).

**Massive.com** (formerly Polygon.io; `MASSIVE_API_KEY`): quote snapshot, price bars, option chain
with IV and Greeks, news with sentiment, and off-exchange prints (trades reported to FINRA TRFs,
i.e. dark pool and internalized volume). What each returns depends on your Massive plan.

Sources that aren't set up are marked in Tools and Roles and skipped at run time with the reason.
**Order placement is always blocked**: any MCP tool that places, changes or cancels orders, or
moves money, is never exposed to the agents (`TRADING_TOOL` in `backend/app/tools/registry.py`).

To add a vendor (e.g. a dark pool feed): put its key in `.env`, add a Connection naming that
variable, then add Data sources on it. MCP servers work with no code; REST APIs need a small
built-in Python tool.

### Connecting IBKR or Bigdata.com (MCP)

Edit the tool in **Tools**: set `url` to the server's MCP endpoint, put the token in `.env` under the
variable named in *Secret environment variable name*, then enable it and tick it on the roles that
should use it. For a local MCP server, use `{"transport": "stdio", "command": "...", "args": [...]}`.

### Adding a Python tool

Add a function and schema to `PYTHON_TOOLS` in `backend/app/tools/python_tools.py`, then add a
Tool with kind `python` and `{"function": "<name>"}`.

## Data sources

Built-in market data uses Yahoo Finance via `yfinance`: free and good enough for research, but
delayed and unofficial. Use the IBKR MCP server for real-time quotes and options data.

## Roadmap

- Trace view in the UI for runs (the API already records them)
- Keep MCP connections open between turns (they reconnect per run today)
- Compaction for very long sessions
- Scheduled screeners and alerts
- AWS deployment: Postgres via `DATABASE_URL`, secrets from Secrets Manager as env vars, and the
  frontend built as static files

This tool is for research and education. It is not financial advice.
