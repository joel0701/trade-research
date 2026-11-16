"""Turns a role's configured tools into API tool definitions plus executors.

A Toolbox is built per role run. It is an async context manager because MCP
servers hold a live connection for the duration of the run.
"""
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from app.models import Tool
from app.tools.python_tools import PYTHON_TOOLS

log = logging.getLogger(__name__)

Executor = Callable[[dict[str, Any]], Awaitable[str]]
MAX_RESULT_CHARS = 40_000  # keep any single tool result from flooding context


def safe_name(name: str) -> str:
    """API tool names must match ^[a-zA-Z0-9_-]{1,64}$."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


class Toolbox:
    def __init__(self, tools: list[Tool]):
        self._tools = [t for t in tools if t.enabled and t.kind != "model"]
        self.definitions: list[dict[str, Any]] = []
        self.executors: dict[str, Executor] = {}
        self._stack = AsyncExitStack()

    def add(self, definition: dict[str, Any], executor: Executor | None = None) -> None:
        self.definitions.append(definition)
        if executor is not None:
            self.executors[definition["name"]] = executor

    async def __aenter__(self) -> "Toolbox":
        for tool in self._tools:
            try:
                if tool.kind == "python":
                    self._add_python(tool)
                elif tool.kind == "claude_server_tool":
                    self.definitions.append(dict(tool.config))  # runs on Anthropic's side
                elif tool.kind == "mcp_server":
                    await self._add_mcp(tool)
                else:
                    log.warning("Unknown tool kind %s for %s", tool.kind, tool.slug)
            except Exception:
                # One broken tool should not take down the whole run.
                log.exception("Failed to load tool %s", tool.slug)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run a client-side tool. Returns (result_text, is_error)."""
        fn = self.executors.get(name)
        if fn is None:
            return f"Unknown tool: {name}", True
        try:
            out = await fn(args)
            if len(out) > MAX_RESULT_CHARS:
                out = out[:MAX_RESULT_CHARS] + "\n...[truncated]"
            return out, False
        except Exception as e:  # report tool failures to the model rather than crashing
            log.exception("Tool %s failed", name)
            return f"Tool error: {type(e).__name__}: {e}", True

    # ---- adapters -------------------------------------------------------

    def _add_python(self, tool: Tool) -> None:
        fn_name = tool.config.get("function", tool.slug)
        spec = PYTHON_TOOLS.get(fn_name)
        if spec is None:
            raise ValueError(f"No built-in python tool named {fn_name!r}")

        async def run(args: dict[str, Any], _fn=spec.fn) -> str:
            return await _fn(**args)

        self.add(
            {"name": safe_name(tool.slug), "description": tool.description or spec.description,
             "input_schema": spec.input_schema},
            run,
        )

    async def _add_mcp(self, tool: Tool) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        cfg = tool.config
        secret = os.getenv(tool.secret_env) if tool.secret_env else None
        if cfg.get("transport", "stdio") == "http":
            headers = dict(cfg.get("headers", {}))
            if secret:
                headers[cfg.get("secret_header", "Authorization")] = cfg.get("secret_prefix", "Bearer ") + secret
            http = await self._stack.enter_async_context(create_mcp_http_client(headers=headers))
            streams = await self._stack.enter_async_context(streamable_http_client(cfg["url"], http_client=http))
        else:
            env = {**os.environ, **cfg.get("env", {})}
            if secret and tool.secret_env:
                env[tool.secret_env] = secret
            params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []), env=env)
            streams = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()

        allowed = set(cfg.get("allowed_tools") or [])
        blocked = set(cfg.get("blocked_tools") or [])
        listed = await session.list_tools()
        for t in listed.tools:
            if (allowed and t.name not in allowed) or t.name in blocked:
                continue

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
                {"name": safe_name(f"{tool.slug}__{t.name}"), "description": (t.description or t.name)[:1024],
                 "input_schema": schema},
                run,
            )
