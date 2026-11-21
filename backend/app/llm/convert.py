"""Conversation format conversion.

Sessions are always stored in Anthropic's content-block format (text, tool_use,
tool_result, document, image, thinking...). For OpenAI-compatible vendors we convert
that history to chat-completions messages, and convert their replies back, so a session
can switch vendors at any point.
"""
import base64
import io
import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)


def _pdf_text(b64: str, title: str) -> str:
    """Non-Claude models get a PDF's extracted text (Claude reads the PDF itself)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(base64.b64decode(b64)))
        pages = [f"[page {i + 1}]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages)]
        text = "\n\n".join(pages).strip()
        return text or f"[{title}: no extractable text, e.g. a scanned PDF]"
    except Exception as e:
        log.warning("PDF text extraction failed for %s: %s", title, e)
        return f"[{title}: could not read this PDF]"


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", json.dumps(b)) if isinstance(b, dict) else str(b) for b in content)
    return json.dumps(content, default=str)


def _user_parts(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for b in blocks:
        t = b.get("type")
        if t == "text":
            parts.append({"type": "text", "text": b["text"]})
        elif t == "image" and b.get("source", {}).get("type") == "base64":
            src = b["source"]
            parts.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
        elif t == "document":
            src, title = b.get("source", {}), b.get("title", "document")
            body = src.get("data", "") if src.get("type") == "text" else _pdf_text(src.get("data", ""), title)
            parts.append({"type": "text", "text": f"<document title=\"{title}\">\n{body}\n</document>"})
    return parts


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "user":
            # Tool results become role="tool" messages, which must directly follow the call.
            for b in content:
                if b.get("type") == "tool_result":
                    text = _tool_result_text(b.get("content", ""))
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": ("ERROR: " + text) if b.get("is_error") else text})
            parts = _user_parts(content)
            if parts:
                only_text = all(p["type"] == "text" for p in parts)
                out.append({"role": "user",
                            "content": "\n\n".join(p["text"] for p in parts) if only_text else parts})
        elif m["role"] == "assistant":
            # Thinking, server-tool and fallback blocks are Claude-specific and dropped.
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [openai_call(b) for b in content if b.get("type") == "tool_use"]
            if not text and not calls:
                continue
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif m["role"] == "system":
            out.append({"role": "system", "content": _tool_result_text(content)})
    return out


# Extra fields a vendor attaches to a tool call that must be sent back with it, e.g. Gemini 3+
# models' thought signature ({"google": {"thought_signature": ...}}); without it, the next request
# after a tool call is rejected. Kept on the stored tool_use block under this key, and removed
# before a conversation is sent to Claude (see strip_vendor_extra).
VENDOR_EXTRA = "vendor_extra"


def vendor_extra(tool_call_delta: Any) -> dict[str, Any] | None:
    extra = getattr(tool_call_delta, "extra_content", None)
    if extra is None:
        extra = (getattr(tool_call_delta, "model_extra", None) or {}).get("extra_content")
    if extra is not None and hasattr(extra, "model_dump"):
        extra = extra.model_dump()
    return extra if isinstance(extra, dict) and extra else None


def openai_call(block: dict[str, Any]) -> dict[str, Any]:
    """A stored tool_use block as a chat-completions tool call (with any vendor extras)."""
    call: dict[str, Any] = {"id": block["id"], "type": "function",
                            "function": {"name": block["name"], "arguments": json.dumps(block.get("input", {}))}}
    if block.get(VENDOR_EXTRA):
        call["extra_content"] = block[VENDOR_EXTRA]
    return call


def strip_vendor_extra(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation without other vendors' tool-call extras (Claude rejects unknown fields)."""
    if not any(isinstance(m.get("content"), list) and any(isinstance(b, dict) and VENDOR_EXTRA in b for b in m["content"])
               for m in messages):
        return messages
    return [{**m, "content": [{k: v for k, v in b.items() if k != VENDOR_EXTRA} if isinstance(b, dict) else b
                              for b in m["content"]]} if isinstance(m.get("content"), list) else m
            for m in messages]


def to_openai_tools(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Client tools only; Claude server tools (web_search etc.) have no equivalent here."""
    return [{"type": "function", "function": {"name": d["name"], "description": d.get("description", ""),
                                              "parameters": d.get("input_schema", {"type": "object"})}}
            for d in definitions if "input_schema" in d]


def safe_tool_id(raw: str | None, n: int) -> str:
    """Tool-call ids must also be valid for Claude if the session switches back."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw or "")
    return cleaned or f"call_{n}"
