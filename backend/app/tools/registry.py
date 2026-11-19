"""Turns a role's configured tools into API tool definitions plus executors.

A Toolbox is built per role run. It is an async context manager because MCP servers hold a
live connection for the duration of the run. Data sources that share a Connection (e.g.
IBKR quotes, IBKR option chains, IBKR account) share one MCP session.
"""
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from sqlmodel import Session, select

from app.models import Connection, Tool
from app.tools.python_tools import PYTHON_TOOLS

log = logging.getLogger(__name__)

Executor = Callable[[dict[str, Any]], Awaitable[str]]
from app.tools.compact import PAGE_CHARS, compact_text, find_in, page  # noqa: E402

READ_MORE = {
    "name": "read_more",
    "description": ("Read more of a long tool result that was split into pages (its note gives the result_id). "
                    "Use offset to read the next page, or find to get the passages that mention a keyword."),
    "input_schema": {"type": "object", "properties": {
        "result_id": {"type": "string"},
        "offset": {"type": "integer", "description": "Character offset to read from"},
        "find": {"type": "string", "description": "Keyword or phrase to search for in the full result"}},
        "required": ["result_id"]},
}

# Anything that can place, change or cancel orders (or move money) is never exposed to the
# agents, whatever a source's allowed_tools says. Trading needs a per-order confirmation flow first.
TRADING_TOOL = re.compile(
    r"(create|place|submit|modify|update|replace|cancel|delete|transmit)_?\w*(order|trade|instruction)"
    r"|(order|trade)_?(create|place|submit|modify|cancel|delete)|transfer|withdraw", re.I)


def safe_name(name: str) -> str:
    """API tool names must match ^[a-zA-Z0-9_-]{1,64}$."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


def is_trading_tool(name: str) -> bool:
    return bool(TRADING_TOOL.search(name))


def availability(tool: Tool, conn: Connection | None) -> tuple[bool, str | None]:
    """Whether a data source can be used now, and why not."""
    if not tool.enabled:
        return False, "disabled"
    if tool.connection_id is not None:
        if conn is None:
            return False, "its connection was deleted"
        if not conn.enabled:
            return False, f"connection {conn.name} is disabled"
        if conn.secret_env and not os.getenv(conn.secret_env):
            return False, f"set {conn.secret_env} in .env"
        if conn.kind == "mcp" and "REPLACE" in json.dumps(conn.config):
            return False, f"set the server address on connection {conn.name}"
        if conn.kind == "tws":
            from app.tools.ibkr_gateway import gateway_reachable, settings

            if not gateway_reachable():
                host, port, _ = settings()
                return False, f"IB Gateway isn't running or API access is off (expected at {host}:{port})"
    if tool.secret_env and not os.getenv(tool.secret_env):
        return False, f"set {tool.secret_env} in .env"
    if tool.kind == "python" and tool.config.get("function", tool.slug) not in PYTHON_TOOLS:
        return False, f"no built-in function {tool.config.get('function')!r}"
    return True, None


def load_connections() -> dict[int, Connection]:
    from app.db import engine

    with Session(engine) as db:
        return {c.id: c for c in db.exec(select(Connection)).all()}


class Toolbox:
    def __init__(self, tools: list[Tool], connections: dict[int, Connection] | None = None):
        self._requested = [t for t in tools if t.kind != "model"]
        self._connections = connections
        self.definitions: list[dict[str, Any]] = []
        self.executors: dict[str, Executor] = {}
        self.skipped: list[tuple[str, str]] = []  # (tool name, reason) for the trace/logs
        self._stack = AsyncExitStack()
        self._results: dict[str, str] = {}  # long results, kept whole so read_more can page through them

    def add(self, definition: dict[str, Any], executor: Executor | None = None) -> None:
        self.definitions.append(definition)
        if executor is not None:
            self.executors[definition["name"]] = executor

    async def __aenter__(self) -> "Toolbox":
        conns = self._connections if self._connections is not None else load_connections()
        usable = []
        for tool in self._requested:
            ok, why = availability(tool, conns.get(tool.connection_id) if tool.connection_id else None)
            if ok:
                usable.append(tool)
            else:
                self.skipped.append((tool.name, why or "unavailable"))
        if self.skipped:
            log.info("Skipping unavailable tools: %s", self.skipped)

        mcp_groups: dict[tuple[str, int], list[Tool]] = {}
        for tool in usable:
            try:
                if tool.kind == "python":
                    self._add_python(tool)
                elif tool.kind == "claude_server_tool":
                    self.definitions.append(dict(tool.config))  # runs on Anthropic's side
                elif tool.kind == "mcp_server":
                    key = ("conn", tool.connection_id) if tool.connection_id else ("tool", tool.id)
                    mcp_groups.setdefault(key, []).append(tool)
                else:
                    log.warning("Unknown tool kind %s for %s", tool.kind, tool.slug)
            except Exception:
                # One broken tool should not take down the whole run.
                log.exception("Failed to load tool %s", tool.slug)

        for (kind, ident), group in mcp_groups.items():
            conn = conns.get(ident) if kind == "conn" else None
            try:
                await self._add_mcp_group(group, conn)
            except Exception:
                log.exception("Failed to connect to MCP server for %s", [t.slug for t in group])
                self.skipped.extend((t.name, "connection failed") for t in group)
        self.add(READ_MORE, self._read_more)
        return self

    async def _read_more(self, args: dict[str, Any]) -> str:
        full = self._results.get(str(args.get("result_id", "")))
        if full is None:
            return "Unknown result_id. Use the id given in a long result's note."
        if args.get("find"):
            return find_in(full, str(args["find"]))
        return page(full, str(args["result_id"]), int(args.get("offset") or 0))

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def execute(self, name: str, args: dict[str, Any], meta: dict[str, Any] | None = None) -> tuple[str, bool]:
        """Run a client-side tool. Returns (result_text, is_error). JSON results are compacted, and
        long results are split into pages (read_more) instead of being cut off. meta gets the sizes:
        raw_chars (as the tool returned it) and chars (what the model receives)."""
        fn = self.executors.get(name)
        if fn is None:
            return f"Unknown tool: {name}", True
        try:
            raw = await fn(args)
            out = raw
            if name != "read_more":
                out = compact_text(raw)
                if len(out) > PAGE_CHARS:
                    rid = f"r{len(self._results) + 1}"
                    self._results[rid] = out
                    out = page(out, rid)
            if meta is not None:
                meta.update(raw_chars=len(raw), chars=len(out))
            return out, False
        except Exception as e:  # report tool failures to the model rather than crashing
            log.exception("Tool %s failed", name)
            return f"Tool error: {type(e).__name__}: {e}", True

    # ---- adapters -------------------------------------------------------

    def _add_python(self, tool: Tool) -> None:
        fn_name = tool.config.get("function", tool.slug)
        spec = PYTHON_TOOLS[fn_name]

        async def run(args: dict[str, Any], _fn=spec.fn) -> str:
            return await _fn(**args)

        description = tool.description or spec.description
        if tool.notes:  # limits the agent should know about (e.g. a fixed lookback)
            description += f" Notes: {tool.notes}"
        self.add({"name": safe_name(tool.slug), "description": description[:1024], "input_schema": spec.input_schema}, run)

    async def open_mcp(self, cfg: dict[str, Any], secret_env: str | None):
        """Open an MCP client session (kept open until the toolbox closes)."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        secret = os.getenv(secret_env) if secret_env else None
        if cfg.get("transport", "http") == "http":
            headers = dict(cfg.get("headers", {}))
            if secret:
                headers[cfg.get("secret_header", "Authorization")] = cfg.get("secret_prefix", "Bearer ") + secret
            http = await self._stack.enter_async_context(create_mcp_http_client(headers=headers))
            streams = await self._stack.enter_async_context(streamable_http_client(cfg["url"], http_client=http))
        else:
            env = {**os.environ, **cfg.get("env", {})}
            if secret and secret_env:
                env[secret_env] = secret
            params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []), env=env)
            streams = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        return session

    async def _add_mcp_group(self, group: list[Tool], conn: Connection | None) -> None:
        """One session per connection; expose the union of what each source allows."""
        if conn is not None:
            session = await self.open_mcp(conn.config, conn.secret_env)
            prefix, allow_trading = conn.slug, bool(conn.config.get("allow_trading"))
        else:  # older single-tool MCP configuration
            tool = group[0]
            session = await self.open_mcp(tool.config, tool.secret_env)
            prefix, allow_trading = tool.slug, False

        listed = await session.list_tools()
        exposed: dict[str, Any] = {}
        for t in listed.tools:
            if is_trading_tool(t.name) and not allow_trading:
                continue
            for source in group:
                allowed = set(source.config.get("allowed_tools") or [])
                blocked = set(source.config.get("blocked_tools") or [])
                if (not allowed or t.name in allowed) and t.name not in blocked:
                    exposed[t.name] = t
                    break

        for t in exposed.values():
            async def run(args: dict[str, Any], _name=t.name) -> str:
                result = await session.call_tool(_name, args)
                parts = []
                for c in getattr(result, "content", []) or []:
                    parts.append(c.text if getattr(c, "type", "") == "text" else json.dumps(c.model_dump(), default=str))
                text = "\n".join(parts) or json.dumps(getattr(result, "structuredContent", None), default=str)
                if getattr(result, "isError", False) or getattr(result, "is_error", False):
                    raise RuntimeError(text)
                return text

            schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {"type": "object"}
            self.add(
                {"name": safe_name(f"{prefix}__{t.name}"), "description": (t.description or t.name)[:1024],
                 "input_schema": schema},
                run,
            )
