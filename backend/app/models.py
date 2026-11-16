"""Database tables.

Everything that defines agent behaviour (tools, roles, skills, screeners) is data,
editable from the UI, so adding a role or tool needs no code change.
"""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(UTC)


class Tool(SQLModel, table=True):
    """A capability a role can use.

    kind:
      model              -> a Claude model; config: model_id, effort, max_tokens, fallbacks
      python             -> a built-in Python function; config: function
      claude_server_tool -> an Anthropic-hosted tool (web search/fetch); config: the tool dict
      mcp_server         -> an MCP server; config: transport (stdio|http), command/args or url,
                            headers, allowed_tools / blocked_tools
    Secrets never go in config: put the env var *name* in secret_env.
    """

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    kind: str
    description: str = ""
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
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


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
    created_at: datetime = Field(default_factory=now)


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
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None


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
    created_at: datetime = Field(default_factory=now)
