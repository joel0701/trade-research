"""Mock LLM: canned responses that exercise the whole app without calling (or paying for) a model.

Turned on for every role with MOCK_LLM=1 in .env, or per session by choosing the "Mock LLM" model.
- The Lead writes a short note, consults up to two specialists (really running their loops),
  then streams a canned final answer that says it is a mock and quotes what specialists found.
- Specialists really call one of their data tools (so data connections are exercised) and return
  a short canned summary with a snippet of the real result.
Messages, trace runs, notes and the UI behave exactly as for a real model; token usage is zero.
"""
import asyncio
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.loop import Emit, LoopResult, ModelConfig, OnMessage
    from app.tools.registry import Toolbox

MOCK_MODEL_ID = "mock"
NOT_TICKERS = {"I", "A", "AN", "AND", "THE", "OR", "OF", "TO", "IN", "ON", "FOR", "IS", "IT", "ME", "MY", "US", "USA",
               "IBKR", "RSI", "MACD", "ETF", "IV", "EPS", "CEO", "SEC", "ATS", "FINRA", "AI", "PE", "YTD", "UTC", "ET",
               "NYSE", "AMEX", "OTC", "TRF", "OK", "Q1", "Q2", "Q3", "Q4", "FY", "DCF", "API", "LLM", "RUN", "TASK"}


def mock_mode() -> bool:
    """MOCK_LLM=1 in .env: every role uses the mock, whatever model is configured."""
    return os.getenv("MOCK_LLM", "").strip().lower() in ("1", "true", "yes", "on")


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _question(messages: list[dict[str, Any]]) -> str:
    """The latest real question (skipping tool results and hidden app prompts' repetition)."""
    for m in reversed(messages):
        if m["role"] == "user":
            text = _text_of(m["content"]).strip()
            if text:
                return text
    return ""


def guess_ticker(text: str) -> str:
    for word in re.findall(r"\b[A-Z]{1,5}\b", text.replace("*", "")):
        if word not in NOT_TICKERS and len(word) >= 2:
            return word
    return "SPY"


async def _stream(text: str, emit: "Emit", source: str, stream_text: bool) -> None:
    if not stream_text:
        return
    for chunk in re.findall(r"\S+\s*", text):
        await emit({"type": "text", "source": source, "text": chunk})
        await asyncio.sleep(0.01)


def _record(messages, on_message, role: str, content: list[dict[str, Any]], visible: bool) -> None:
    messages.append({"role": role, "content": content})
    if on_message:
        on_message(role, content, visible, {"input_tokens": 0, "output_tokens": 0} if role == "assistant" else None)


async def _call_tools(calls: list[dict[str, Any]], toolbox: "Toolbox", emit: "Emit", source: str,
                      result: "LoopResult") -> list[dict[str, Any]]:
    async def one(c: dict[str, Any]) -> dict[str, Any]:
        await emit({"type": "tool_call", "source": source, "name": c["name"], "input": c["input"]})
        out, is_error = await toolbox.execute(c["name"], c["input"])
        result.tool_calls.append({"name": c["name"], "input": c["input"], "is_error": is_error, "output_preview": out[:500]})
        return {"type": "tool_result", "tool_use_id": c["id"], "content": out, "is_error": is_error}
    return list(await asyncio.gather(*(one(c) for c in calls)))


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{4,}", text.lower())}


def pick_specialists(question: str, toolbox: "Toolbox", n: int) -> list[str]:
    """The specialists whose name and description best match the question (like a real Lead would)."""
    q = _stems(question)
    defs = [d for d in toolbox.definitions if d["name"].startswith("consult_")]
    scored = sorted(defs, key=lambda d: -len(q & _stems(d["name"].replace("_", " ") + " " + d.get("description", ""))))
    return [d["name"] for d in scored[:n]]


def _pick_data_tool(toolbox: "Toolbox") -> dict[str, Any] | None:
    """A client-side data tool that can be called with just a ticker (or no arguments)."""
    for d in toolbox.definitions:
        schema = d.get("input_schema")
        if not schema or d["name"].startswith("consult_"):
            continue
        required = set(schema.get("required", []))
        if required <= {"ticker", "underlying"}:
            return d
    return None


async def run_agent_mock(*, model: "ModelConfig", system: str, messages: list[dict[str, Any]], toolbox: "Toolbox",
                         emit: "Emit", on_message: "OnMessage | None", stream_text: bool, max_iterations: int,
                         source: str, result: "LoopResult") -> "LoopResult":
    question = _question(messages)
    ticker = guess_ticker(question)
    consults = pick_specialists(question, toolbox, 2)

    if consults:  # the Lead
        note = "Checking with the team (mock run, no LLM call). "
        calls = [{"type": "tool_use", "id": f"mock_{i}_{abs(hash(question)) % 10**6}", "name": name,
                  "input": {"question": f"(mock) Look into {ticker} for this question: {question[:300]}"}}
                 for i, name in enumerate(consults)]
        await _stream(note, emit, source, stream_text)
        _record(messages, on_message, "assistant", [{"type": "text", "text": note}, *calls], True)
        tool_results = await _call_tools([{"id": c["id"], "name": c["name"], "input": c["input"]} for c in calls],
                                         toolbox, emit, source, result)
        _record(messages, on_message, "user", tool_results, False)
        findings = "\n".join(f"- **{c['name'].removeprefix('consult_').replace('_', ' ').title()}:** "
                             f"{(r['content'] or '').strip()[:280]}" for c, r in zip(calls, tool_results))
    else:
        findings = ""
        tool = _pick_data_tool(toolbox)
        if tool is not None:  # a specialist: really fetch some data
            props = tool["input_schema"].get("properties", {})
            args = {"underlying": ticker} if "underlying" in props else ({"ticker": ticker} if "ticker" in props else {})
            call = {"id": f"mock_t_{abs(hash(question)) % 10**6}", "name": tool["name"], "input": args}
            _record(messages, on_message, "assistant", [{"type": "tool_use", **call}], False)
            [res] = await _call_tools([call], toolbox, emit, source, result)
            _record(messages, on_message, "user", [res], False)
            findings = f"{tool['name']} returned: {(res['content'] or '').strip()[:400]}"

    if consults:
        final = (f"🧪 **Mock response** (no LLM was called, so this costs nothing).\n\n"
                 f"**Question:** {question[:400]}\n\n"
                 f"**What the team found (real tool data, canned wording):**\n{findings}\n\n"
                 f"| Item | Value |\n|---|---|\n| Ticker | {ticker} |\n| Specialists consulted | {len(consults)} |\n"
                 f"| Model | mock |\n\nSwitch off MOCK_LLM (or choose a real model) for a real analysis.")
    else:
        final = (f"(mock specialist, no LLM call) Summary for {ticker}. "
                 f"{findings or 'No data tool was available to call.'}")
    await _stream(final, emit, source, stream_text)
    _record(messages, on_message, "assistant", [{"type": "text", "text": final}], True)
    result.text, result.stop_reason = final, "end_turn"
    return result


def mock_skill_draft(transcript: str) -> dict[str, Any]:
    """Canned 'Save as skill' draft built from the first question in the transcript."""
    first = next((ln.split(":", 1)[1].strip() for ln in transcript.splitlines() if ln.startswith("User")), "Analyse AAPL")
    ticker = guess_ticker(first)
    template = first.replace("*", "").replace(ticker, "{ticker}") if ticker != "SPY" else first.replace("*", "")
    return {"name": "Mock Drafted Skill", "description": "Drafted by the mock LLM (no cost).",
            "prompt_template": template, "params": [{"name": "ticker", "label": "Ticker", "default": ""}]
            if "{ticker}" in template else []}

