# Trade Research

A multi-agent research assistant for stocks and options. You chat with a **Lead Analyst**,
which consults a team of specialists (Sector Mapper, Finance Analyst, Technical Analyst,
News Analyst, Strategy Optimizer, Research Agent), each with its own LLM model (specific to the task), prompt and tools.
Every chat session is saved and can be reopened and continued. **Skills** (pre-built analyses) and
**Scanners** (rule-based scans, or vendor specific (IBKR market scanners) each start a new session with their results.

Roles, tools, skills and scanners are all data, editable in the app; no code change is needed to
add a role or connect a tool.

## Architecture

```
React UI (Vite)  ──/api──►  FastAPI
                             ├─ Orchestrator: Lead Analyst agent loop
                             │    └─ consult_<role> tools → specialist agent loops (run in parallel)
                             ├─ Tool registry: Claude models, Python functions,
                             │    Claude server tools (web search/fetch), MCP servers (IBKR, Bigdata.com…)
                             ├─ Scanner engine: rules over computed indicators, or IBKR market scanners
                             └─ SQLite (SQLModel): sessions, messages, runs, tools, roles, skills, scanners
```

- **No agent framework.** `backend/app/agents/loop.py` is a small loop over the Anthropic Messages API.
  The Lead and the specialists share it; a specialist is just a tool the Lead can call.
- **Sessions keep the full API history**, including hidden tool calls and thinking blocks, so a
  conversation can be continued exactly. The UI shows only the visible text.
- **Runs** record every Lead and specialist call (input, output, tool calls, token usage):
  `GET /api/sessions/{id}/runs`.
- **Scanners never use the LLM to scan.** Rule-based scanners compute indicators in pandas and apply JSON rules
  (IBKR scanners use IBKR's own ranking, optionally filtered by your rules);
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
and scanners on first start. Delete the file to reset.

Tests (no API calls; Claude is faked):

```bash
cd backend && uv run pytest
```

## Configuring

| Screen | What it controls |
|---|---|
| **Tools** | Models (model id, effort, max tokens, refusal fallbacks), built-in Python functions, Claude web search/fetch, MCP servers |
| **Roles** | Each analyst's model, system prompt, allowed tools, iteration limit; which role is the Lead |
| **Skills** | Prompt templates with parameters, e.g. `{ticker}`; optional data sources; the model the skill runs with |
| **Scanners** | Rule-based (ticker universe + rules) or IBKR market scanner (scan type, market, price/volume/market-cap filters, optional rules); sort; the task for the Lead after the scan and the model that runs it. Settings cover only the scan: the analysis and follow-ups use the full team |

**Skills built on a scanner** (Skills → *Built on a scanner*) chain the steps in one session: the
scan, the scanner's task with the scanner's model, then the skill's prompt evaluating those results
with the skill's model. Follow-up questions use the Research default. The stages run in the
background, so they finish even if you leave the page.

**Suggested next steps:** the final answer of a skill or scanner run ends with one to three specific
follow-up questions based on the data (e.g. the news behind the top gainers' moves, naming the
tickers). They show as clickable suggestions; clicking one puts it in the box to edit or send.
Follow-up answers don't add them.

**Skill and scanner models** (and a skill's data sources) apply to their own run only, so a
cheaper model such as Sonnet can handle steps that need little reasoning. Follow-up questions in
the session always use the Research default: the Lead role's model (or the one picked in the
composer) and the full team.

Default models: Lead and Strategy Optimizer on Claude Opus 5.5; most specialists on Sonnet 5.5;
Sector Mapper on Haiku 5.5. Opus, Sonnet and Fable use server-side refusal fallbacks
(`"fallbacks": "default"` in the model config); remove it to opt out.

### Testing without LLM costs (mock mode)

Set `MOCK_LLM=1` in `.env` and restart: every role uses a free mock instead of a real model. The
Lead consults the most relevant specialists, specialists really call one of their data tools, and
answers are canned (clearly marked) but stream, appear in the trace, footer and timeline as usual,
with $0 cost. A **MOCK LLM** badge shows in the top bar. To mock only one session's Lead, pick
**Mock LLM (no cost)** in the model picker instead.

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

### Local models (Ollama, LM Studio, MLX)

Local models are free and private; nothing leaves your machine. Start a local server, then open
**Tools → Models → Local models**: it lists the models installed on each running server, with
**+ Add**. Added models appear in the model picker while their server runs.

- **Ollama:** `ollama serve`, then `ollama pull qwen2.5:14b` (port 11434).
- **LM Studio:** Developer → Start server (port 1234). On Apple Silicon it runs MLX models.
- **MLX:** `pip install mlx-lm`, then `mlx_lm.server --model mlx-community/Qwen2.5-14B-Instruct-4bit` (port 8080).
- **Anything else OpenAI-compatible** (llama.cpp, vLLM): set `LOCAL_LLM_BASE_URL` in `.env`.

Ports can be changed in `.env` (see `.env.example`). Pick models that support **tool calling**
(e.g. Qwen 2.5/3, Llama 3.1+, Mistral Small) so the Lead can consult the team and specialists can use
data tools. A model without tool calling still answers, but on its own, without the team or data.
Local models are slower than hosted ones, and specialists run in parallel, so smaller models (7-14B)
suit specialist roles best on a laptop.

### Data sources and connections

**Tools → Connections** holds how to reach each provider (endpoint and the name of the `.env`
variable with its key). **Tools → Data sources** lists what each connection provides, grouped by
category: real-time quotes, price history, option chains, fundamentals, SEC filings, insider trading,
dark pool, news, web search, brokerage, screening. One connection can serve several sources: the
IB Gateway connection backs *IBKR quote*, *price bars*, *option chain*, *scanner* and
*account (read-only)*, and sources on one MCP connection share one session.

Built in: Yahoo Finance (free, delayed), DuckDuckGo, Claude web search/fetch, and **SEC EDGAR**
(free): recent filings, reading a filing's text, and Form 4 insider trades. EDGAR needs
`SEC_USER_AGENT` in `.env` with an app name and contact email (an SEC requirement).

**FINRA** (free, no key): daily off-exchange short volume (the "dark pool short ratio", posted by
6 pm ET) and weekly dark pool (ATS) volume by venue with ATS share of off-exchange volume (published
2-4 weeks after each week).

**Massive.com** (formerly Polygon.io; `MASSIVE_API_KEY`): quote snapshot, price bars, option chain
with IV and Greeks, news with sentiment, and off-exchange prints (trades reported to FINRA TRFs,
i.e. dark pool and internalized volume). What each returns depends on your Massive plan.

Each connection has a **Cost** setting (free, free with an account, or paid plan), shown as an
icon on the connection and on every data source it serves.

Each data source has **Notes**: what it supports and its limits (e.g. FINRA short volume's fixed
60-trading-day lookback). Agents see the notes with the tool, and skills and scanners that use a
source get its notes as a **Data notes** callout in their prompt. A skill can name the data sources
it uses (Skills → *Data sources this skill uses*); its sessions then use only those. Don't add a
skill field for something a source fixes; put it in the source's notes instead.

Sources that aren't set up are marked in Tools and Roles. **Before a question runs**, the app
checks every source assigned to the active roles; if any are unavailable it holds the question and
asks in the chat whether to continue, grouped by cause, with the impact: *high* when a role has no
other working source for that kind of data, *low* when another source covers it. You can continue,
continue without being asked again in that session (until the set of unavailable sources changes),
or cancel. Sources you've disabled yourself aren't reported. A skill that names its data sources uses only those for its own run (and only those are
checked). A scanner's settings cover only the scan; its results
are then analysed, and follow-up questions answered, like any research question, with the full team.
**Order placement is always blocked**: any MCP tool that places, changes or cancels orders, or
moves money, is never exposed to the agents (`TRADING_TOOL` in `backend/app/tools/registry.py`).

To add a vendor (e.g. a dark pool feed): put its key in `.env`, add a Connection naming that
variable, then add Data sources on it. MCP servers work with no code; REST APIs need a small
built-in Python tool.

### Interactive Brokers (IB Gateway)

IBKR has no simple API key for individual accounts. The app talks to **IB Gateway** (or TWS) on
your computer through the TWS API, with one read-only connection that reconnects automatically:

1. Install IB Gateway (Stable) from interactivebrokers.com, log in (paper first), and in
   Configure → Settings → API enable *ActiveX and Socket Clients*; keep *Read-Only API* on.
2. If your port isn't 4002 (paper IB Gateway), set `IBKR_PORT` in `.env` (4001 live; TWS 7497/7496).
3. Tools → Connections → *Interactive Brokers (IB Gateway)* shows whether it's reachable.

Sources: quote, price bars, option chain with model IV and Greeks, and account (balances,
positions with P&L, open orders; read-only). Real-time data needs IBKR market data subscriptions;
otherwise IBKR returns delayed data and the result says so. To avoid daily logins, turn on
*Auto restart* in IB Gateway (Configure → Settings → Lock and Exit) and keep the Mac awake.

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
- Scheduled scanners and alerts
- AWS deployment: Postgres via `DATABASE_URL`, secrets from Secrets Manager as env vars, and the
  frontend built as static files

This tool is for research and education. It is not financial advice.
