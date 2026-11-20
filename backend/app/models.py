"""Database tables.

Everything that defines agent behaviour (tools, roles, skills, screeners) is data,
editable from the UI, so adding a role or tool needs no code change.
"""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Timestamps are stored in UTC and always come back marked as UTC.

    SQLite has no time-zone type, so values are saved as naive UTC and tagged on the way
    out. The API then sends ISO 8601 with +00:00 and the UI converts to the user's zone."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class Connection(SQLModel, table=True):
    """How to reach a provider: endpoint and credentials, set up once.

    One connection can back several data sources (Tools): e.g. one IBKR connection serves
    real-time quotes, option chains and account data. kind:
      mcp  -> an MCP server; config: transport (http|stdio), url or command/args, headers, env
      http -> a web API used by built-in Python tools (e.g. SEC EDGAR)
      none -> no setup needed (e.g. Yahoo Finance)
    secret_env names the environment variable that must be set for the connection to work
    (its value lives in .env, never in the database).
    """

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    kind: str = "none"
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    secret_env: str | None = None
    # Cost to use: "free" (no account), "account" (free, but needs an account, e.g. a brokerage),
    # "paid" (needs a paid plan or usage billing), "" (not set). Its data sources show the same icon.
    pricing: str = ""
    enabled: bool = True


class Tool(SQLModel, table=True):
    """A model or data source a role can use.

    kind:
      model              -> an LLM; config: provider, model_id, max_tokens, prices, ...
      python             -> a built-in Python function; config: function
      claude_server_tool -> an Anthropic-hosted tool (web search/fetch); config: the tool dict
      mcp_server         -> tools from an MCP server (normally via connection_id); config:
                            allowed_tools / blocked_tools pick which of the server's tools
    capabilities tags what the source provides (see app.catalog.CAPABILITIES).
    Secrets never go in config: put the env var *name* in secret_env (or on the connection).
    """

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    kind: str
    description: str = ""
    connection_id: int | None = Field(default=None, foreign_key="connection.id")
    capabilities: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # What the source supports and its limits (e.g. a fixed lookback). Shown to the agents with the
    # tool, and added as "Data notes" to skill and scanner prompts that use the source.
    notes: str = ""
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    secret_env: str | None = None
    enabled: bool = True


class Role(SQLModel, table=True):
    """An analyst on the team. The lead is a role with is_lead=True."""

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    description: str = ""  # shown to the Lead so it knows when to consult this role
    is_lead: bool = False
    model_tool_id: int | None = Field(default=None, foreign_key="tool.id")
    system_prompt: str = ""
    tool_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    max_iterations: int = 12
    enabled: bool = True


class ChatSession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = "New research"
    origin: str = "chat"  # chat | skill:<id> | screener:<id>
    # Model the Lead uses in this session (a Tool of kind "model"); None = the Lead role's default.
    lead_model_tool_id: int | None = None
    # Model for the next turn only, then cleared: a skill's or scanner's own model runs its first
    # answer, and follow-up questions use the session's model (normally the Research default).
    next_turn_model_tool_id: int | None = None
    # Data sources for the next turn only (a skill that names its sources), then cleared, so
    # follow-up questions use the full team.
    next_turn_tool_scope: list[int] | None = Field(default=None, sa_column=Column(JSON))
    # Extra instructions for the next turn only (e.g. end a skill's or scanner's answer with
    # suggested next steps), then cleared.
    next_turn_note: str | None = None
    # Stages to run automatically after the current turn, in order, each with its own model:
    # [{"text", "model_tool_id", "tool_scope", "note", "hidden", "label"}]. E.g. a skill built on a scanner: the scanner's
    # analysis (its model), then the skill's evaluation (the skill's model).
    queued_turns: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    # "Don't ask again in this session" for this set of unavailable sources (see app.preflight).
    ack_unavailable: str | None = None
    # When set (e.g. a session started from a scanner), only these data sources are used: each role
    # keeps just the ones it has, and roles left with none aren't offered to the Lead.
    tool_scope: list[int] | None = Field(default=None, sa_column=Column(JSON))
    # A question held back by the unavailable-sources prompt, awaiting the user's decision
    # (kept here so the prompt survives switching sessions): text, attachments, report.
    pending_confirm: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class Message(SQLModel, table=True):
    """One API-level message in the Lead's conversation.

    content is the list of content blocks exactly as the API returned them
    (thinking blocks included) so a session can be continued later.
    visible=False marks internal tool_use / tool_result plumbing that the UI hides.
    """

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="chatsession.id", index=True)
    role: str  # user | assistant | notice (a record shown in the chat, never sent to the model)
    content: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    visible: bool = True
    run_id: int | None = None  # the Lead run that produced this message (links to the trace)
    feedback: str | None = None  # "up" | "down" | None: the user's rating of an answer
    model: str | None = None
    usage: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class Run(SQLModel, table=True):
    """Trace of one role run (Lead turn or specialist consultation)."""

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="chatsession.id", index=True)
    role_slug: str
    model: str | None = None
    parent_run_id: int | None = None
    input: str = ""
    output: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    usage: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "running"  # running | ok | error
    error: str | None = None
    started_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)


class Skill(SQLModel, table=True):
    """A pre-built analysis: a prompt template with parameters, e.g. {ticker}."""

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    description: str = ""
    prompt_template: str
    params: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Optional scanner the skill builds on: running the skill runs the scan and the scanner's
    # analysis (with the scanner's model), then the skill's prompt evaluates them (skill's model).
    screener_id: int | None = None
    lead_model_tool_id: int | None = None  # model for the skill's run (first answer); follow-ups use the default
    # Data sources sessions from this skill use (empty = the full team's sources).
    tool_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    enabled: bool = True


class Screener(SQLModel, table=True):
    """A scanner (shown as "Scanners" in the UI).

    source="rules": your rules over a universe of tickers, using computed indicators.
    source="ibkr":  an IBKR market scanner run through IB Gateway (params: scan_code, location,
                    rows, above_price, above_volume, market_cap_above); results are enriched with
                    the same indicator fields, and rules (optional) filter them further.
    See screeners/engine.py."""

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    description: str = ""
    universe: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    rules: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    source: str = "rules"
    # Rule-based: scan a stock list (its slug, e.g. sp500; see Tools → Stock lists) plus the tickers in
    # `universe`. None = only `universe`.
    universe_index: str | None = None
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # A scanner only scans: a run shows the results (no LLM, no cost). You then ask about them like
    # any research question. To have the Lead evaluate results automatically, build a skill on it.
    sort_by: str | None = None
    limit: int = 25
    # No longer used (scanners don't run the Lead); kept so older databases still load.
    lead_model_tool_id: int | None = None
    follow_up_prompt: str = ""
    enabled: bool = True


class Attachment(SQLModel, table=True):
    """A document uploaded into a session. The file lives on disk under UPLOAD_DIR;
    messages reference it by id and it is expanded into a content block per request."""

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="chatsession.id", index=True)
    message_id: int | None = None  # set once it is sent with a message
    filename: str
    media_type: str
    kind: str  # pdf | image | text
    size: int
    path: str
    created_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class UserProfile(SQLModel, table=True):
    """The person using the app (single row, id=1) and their display preferences."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
    email: str = ""
    avatar_path: str | None = None
    timezone: str = "auto"  # IANA name like "America/New_York", or "auto" = the browser's zone
    hour12: bool = False
    mock_llm: bool = False  # every team member uses the free mock (testing without LLM cost)
    # Default models (Settings): the real model each default points at (Tool ids); None = Haiku/Sonnet/Opus.
    model_low_id: int | None = None
    model_mid_id: int | None = None
    model_high_id: int | None = None
    updated_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)


class StockList(SQLModel, table=True):
    """A named list of tickers, such as an index (S&P 500), for scanners, skills and questions.

    source: "web" (the ticker column of a table on a web page, or of a CSV file: url + column;
    "wikipedia" is the older name) or "manual" (tickers you maintain). refresh: daily / weekly / monthly /
    manual. A list is refreshed only when something uses it and it's older than its schedule allows (no
    AI model involved). symbol: the index or ETF it follows (e.g. SPX, QQQ); "Find source" uses it to
    locate the download, which can move over time."""

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    description: str = ""
    source: str = "manual"
    symbol: str = ""        # index or ETF symbol / name used to find the source
    url: str = ""
    source_note: str = ""   # how and when the source was found (e.g. "Wikipedia search, 2026-11-20")
    # Backup source, used when the main one fails (e.g. the ETF's holdings file first, Wikipedia second).
    fallback_url: str = ""
    fallback_column: str = "Symbol"
    last_source: str = ""   # "main" or "backup": which source the current tickers came from
    source_checked_at: datetime | None = Field(default=None, sa_type=UTCDateTime)  # last automatic ETF search
    column: str = "Symbol"
    refresh: str = "weekly"
    tickers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    refreshed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    last_error: str | None = None
    enabled: bool = True


class DismissedDefault(SQLModel, table=True):
    """A built-in default the user deleted (e.g. a connection or data source). Startup never
    recreates these. kind is the table name: tool, connection, screener, skill."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    slug: str
