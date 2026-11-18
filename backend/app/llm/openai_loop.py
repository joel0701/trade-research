"""Agent loop for OpenAI-compatible vendors (OpenAI, Gemini, Grok, Llama, Mistral, DeepSeek).

Same contract as app.agents.loop.run_agent: streams text to the UI, runs client-side
tools in parallel, and reports every new message in the stored (Anthropic block) format.
"""
import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from app.config import MAX_LOOP_ITERATIONS
from app.llm.convert import safe_tool_id, to_openai_messages, to_openai_tools
from app.llm.providers import Provider

if TYPE_CHECKING:
    from app.agents.loop import Emit, LoopResult, ModelConfig, OnMessage
    from app.tools.registry import Toolbox

log = logging.getLogger(__name__)
_clients: dict[str, Any] = {}


def openai_client(provider: Provider):
    """One client per vendor, created on first use."""
    if provider.slug not in _clients:
        from openai import AsyncOpenAI

        # Local servers ignore the key, but the SDK requires one.
        _clients[provider.slug] = AsyncOpenAI(api_key=provider.key() or "local", base_url=provider.url())
    return _clients[provider.slug]


def _usage(total: dict[str, int], u: Any) -> None:
    if u is None:
        return
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    total["input_tokens"] = total.get("input_tokens", 0) + (getattr(u, "prompt_tokens", 0) or 0) - cached
    total["cache_read_input_tokens"] = total.get("cache_read_input_tokens", 0) + cached
    total["output_tokens"] = total.get("output_tokens", 0) + (getattr(u, "completion_tokens", 0) or 0)


async def run_agent_openai(*, provider: Provider, model: "ModelConfig", system: str,
                           messages: list[dict[str, Any]], toolbox: "Toolbox", emit: "Emit",
                           on_message: "OnMessage | None", stream_text: bool, max_iterations: int,
                           source: str, result: "LoopResult") -> "LoopResult":
    client = openai_client(provider)
    chat = to_openai_messages(system, messages)
    tools = to_openai_tools(toolbox.definitions)
    skipped = [d.get("name") for d in toolbox.definitions if "input_schema" not in d]
    if skipped:
        log.info("%s: Claude-only tools not available on %s: %s", source, provider.name, skipped)
    params: dict[str, Any] = {"model": model.model_id, "stream": True, **model.params,
                              provider.max_tokens_param: model.max_tokens}
    if tools:
        params["tools"] = tools
    if provider.stream_usage:
        params["stream_options"] = {"include_usage": True}

    for _ in range(min(max_iterations, MAX_LOOP_ITERATIONS)):
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish = None
        usage = None
        try:
            stream = await client.chat.completions.create(messages=chat, **params)
        except Exception as e:
            # Some local models can't call tools (Ollama: "does not support tools"). Answer without them.
            if "tools" in params and "support tools" in str(e).lower():
                log.warning("%s: %s can't call tools; answering without the team or data tools", source, model.model_id)
                params.pop("tools")
                chat[0]["content"] += ("\n\nNote: this model can't call tools, so no specialists or data tools are "
                                       "available. Answer from what you know and say so briefly.")
                stream = await client.chat.completions.create(messages=chat, **params)
            else:
                raise
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            for choice in chunk.choices or []:
                d = choice.delta
                if getattr(d, "content", None):
                    text_parts.append(d.content)
                    if stream_text:
                        await emit({"type": "text", "source": source, "text": d.content})
                for tc in getattr(d, "tool_calls", None) or []:
                    slot = calls.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason
        _usage(result.usage, usage)

        text = "".join(text_parts)
        tool_uses = []
        for n, slot in sorted(calls.items()):
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": slot["args"]}
            tool_uses.append({"type": "tool_use", "id": safe_tool_id(slot["id"], n), "name": slot["name"],
                              "input": args if isinstance(args, dict) else {"value": args}})

        content = ([{"type": "text", "text": text}] if text else []) + tool_uses
        messages.append({"role": "assistant", "content": content})
        if on_message:
            on_message("assistant", content, bool(text.strip()), {
                "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0})
        chat.append({"role": "assistant", "content": text or None, **({"tool_calls": [
            {"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": json.dumps(t["input"])}}
            for t in tool_uses]} if tool_uses else {})})
        result.stop_reason = finish
        if text:
            result.text = text

        if finish == "content_filter":
            result.error = f"{provider.name} declined this request."
            break
        if not tool_uses:
            break
        if finish == "length":
            result.error = "Response hit max_tokens while writing a tool call."
            break

        async def run_one(t: dict[str, Any]) -> dict[str, Any]:
            await emit({"type": "tool_call", "source": source, "name": t["name"], "input": t["input"]})
            if "_invalid_json" in t["input"]:
                out, is_error = "Tool arguments were not valid JSON; please retry.", True
            else:
                out, is_error = await toolbox.execute(t["name"], t["input"])
            result.tool_calls.append({"name": t["name"], "input": t["input"], "is_error": is_error,
                                      "output_preview": out[:500]})
            return {"type": "tool_result", "tool_use_id": t["id"], "content": out, "is_error": is_error}

        tool_results = list(await asyncio.gather(*(run_one(t) for t in tool_uses)))
        messages.append({"role": "user", "content": tool_results})
        if on_message:
            on_message("user", tool_results, False, None)
        for r in tool_results:
            chat.append({"role": "tool", "tool_call_id": r["tool_use_id"],
                         "content": ("ERROR: " if r["is_error"] else "") + r["content"]})
    else:
        result.error = "Stopped: reached the iteration limit."
    return result
