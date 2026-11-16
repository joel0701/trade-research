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

Backend (http://localhost:8000, API docs at /docs):

```bash
cd backend && uv sync && uv run uvicorn app.main:app --reload
```

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
