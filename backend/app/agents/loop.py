"""The agent loop shared by the Lead and every specialist.

A thin manual loop over the Messages API: stream a turn, run any client-side tool
calls (in parallel), feed results back, repeat until the model is done. Kept manual
(rather than the SDK tool runner) so we control streaming to the UI, persistence of
every message, and dispatch to specialist sub-agents.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic

from app.config import MAX_LOOP_ITERATIONS
from app.llm.mock_loop import mock_mode, run_agent_mock
from app.llm.openai_loop import run_agent_openai
from app.llm.providers import provider_of
from app.tools.registry import Toolbox

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
OnMessage = Callable[[str, list[dict[str, Any]], bool, dict[str, Any] | None], None]

_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY
    return _client


@dataclass
class ModelConfig:
    model_id: str = "claude-opus-5-5"
    provider: str = "anthropic"  # see app.llm.providers
    effort: str | None = "high"  # Claude only
    max_tokens: int = 32000
    fallbacks: Any = None  # Claude only: "default" -> server-side refusal fallback
    show_thinking: bool = False  # Claude only
    params: dict[str, Any] = field(default_factory=dict)  # extra request params (other vendors)

    @classmethod
    def from_tool_config(cls, cfg: dict[str, Any]) -> "ModelConfig":
        known = {k: v for k, v in cfg.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class LoopResult:
    text: str = ""
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _add_usage(total: dict[str, int], usage: Any) -> None:
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        total[k] = total.get(k, 0) + (getattr(usage, k, 0) or 0)


async def run_agent(
    *,
    model: ModelConfig,
    system: str,
    messages: list[dict[str, Any]],
    toolbox: Toolbox,
    emit: Emit,
    on_message: OnMessage | None = None,
    stream_text: bool = True,
    max_iterations: int = 12,
    source: str = "lead",
) -> LoopResult:
    """Run the loop, mutating `messages` in place. Calls on_message for each new message."""
    result = LoopResult()
    provider = provider_of({"provider": model.provider})
    if mock_mode() or provider.api == "mock":
        return await run_agent_mock(model=model, system=system, messages=messages, toolbox=toolbox, emit=emit,
                                    on_message=on_message, stream_text=stream_text, max_iterations=max_iterations,
                                    source=source, result=result)
    if not provider.available():
        raise RuntimeError(f"No API key for {provider.name}: set {provider.key_env} in .env "
                           f"to use {model.model_id}.")
    if provider.api != "anthropic":
        return await run_agent_openai(provider=provider, model=model, system=system, messages=messages,
                                      toolbox=toolbox, emit=emit, on_message=on_message, stream_text=stream_text,
                                      max_iterations=max_iterations, source=source, result=result)
    params: dict[str, Any] = {
        "model": model.model_id,
        "max_tokens": model.max_tokens,
        "system": system,
        "cache_control": {"type": "ephemeral"},  # cache the growing conversation prefix
    }
    if toolbox.definitions:
        params["tools"] = toolbox.definitions
    if model.effort:
        params["output_config"] = {"effort": model.effort}
    if model.show_thinking:
        params["thinking"] = {"type": "adaptive", "display": "summarized"}
    if model.fallbacks:
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = model.fallbacks

    for _ in range(min(max_iterations, MAX_LOOP_ITERATIONS)):
        async with client().beta.messages.stream(messages=messages, **params) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta" and stream_text:
                        await emit({"type": "text", "source": source, "text": event.delta.text})
                    elif event.delta.type == "thinking_delta" and stream_text:
                        await emit({"type": "thinking", "source": source, "text": event.delta.thinking})
                elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                    await emit({"type": "tool_call", "source": source, "name": event.content_block.name})
            final = await stream.get_final_message()

        _add_usage(result.usage, final.usage)
        content = [b.to_dict() for b in final.content]
        text = "".join(b.text for b in final.content if b.type == "text")
        tool_uses = [b for b in final.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": content})
        if on_message:
            on_message("assistant", content, bool(text.strip()), final.usage.to_dict())
        result.stop_reason = final.stop_reason
        if text:
            result.text = text  # the last turn's text is the answer

        if final.stop_reason == "pause_turn":  # long server-tool turn: send it back to continue
            continue
        if final.stop_reason == "refusal":
            result.error = "The model declined this request."
            break
        if not tool_uses:
            break
        if final.stop_reason == "max_tokens":
            result.error = "Response hit max_tokens while writing a tool call."
            break

        async def run_one(block: Any) -> dict[str, Any]:
            await emit({"type": "tool_call", "source": source, "name": block.name, "input": block.input})
            out, is_error = await toolbox.execute(block.name, block.input if isinstance(block.input, dict) else {})
            result.tool_calls.append({"name": block.name, "input": block.input, "is_error": is_error,
                                      "output_preview": out[:500]})
            return {"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error}

        # All results go back in ONE user message (keeps parallel tool use working).
        tool_results = list(await asyncio.gather(*(run_one(b) for b in tool_uses)))
        messages.append({"role": "user", "content": tool_results})
        if on_message:
            on_message("user", tool_results, False, None)
    else:
        result.error = "Stopped: reached the iteration limit."

    return result
