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
    role: str  # user | assistant
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
    lead_model_tool_id: int | None = None  # default Lead model for sessions this skill starts
    enabled: bool = True


class Screener(SQLModel, table=True):
    """A deterministic scan: a universe of tickers plus rules. See screeners/engine.py."""

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    description: str = ""
    universe: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    rules: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    sort_by: str | None = None
    limit: int = 25
    follow_up_prompt: str = (
        "Review these screener results. Pick the most promising candidates and explain why, "
        "noting risks and any options strategies worth considering."
    )
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
    updated_at: datetime = Field(default_factory=now, sa_type=UTCDateTime)
