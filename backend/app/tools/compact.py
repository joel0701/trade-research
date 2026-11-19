"""Make tool results smaller without losing information.

- JSON: lists of similar objects become a table ({"columns": [...], "rows": [[...]]}, field names once
  instead of on every row); empty fields are dropped; floats are rounded (4 decimals at or above 1,
  4 significant digits below 1); whitespace removed.
- Long results are split into pages instead of being cut off. The agent gets the first page and a note
  saying how much is left and how to read on (read_more tool), or to search the full result.
"""
import json
import math
import re
from typing import Any

PAGE_CHARS = 16_000  # about 4,000 tokens per page
MAX_FIND_CHARS = 8_000


def _num(x: float) -> float | int | None:
    if math.isnan(x) or math.isinf(x):
        return None
    if x == int(x) and abs(x) < 1e15:
        return int(x)
    return round(x, 4) if abs(x) >= 1 else float(f"{x:.4g}")


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {} or (isinstance(v, float) and math.isnan(v))


def _compact(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, float):
        return _num(v)
    if isinstance(v, dict):
        return {k: _compact(x) for k, x in v.items() if not _empty(x)}
    if isinstance(v, list):
        if len(v) >= 3 and all(isinstance(x, dict) for x in v):
            cols: list[str] = []
            for row in v:
                for k, x in row.items():
                    if k not in cols and not _empty(x):
                        cols.append(k)
            # Only worth it when rows share most of their fields (a real table).
            shared = sum(len(row) for row in v) / (len(v) * max(1, len(cols)))
            if cols and shared >= 0.6:
                return {"columns": cols, "rows": [[_compact(row.get(c)) for c in cols] for row in v]}
        return [_compact(x) for x in v]
    return v


def compact_text(text: str) -> str:
    """The compact form of a JSON result (unchanged if it isn't JSON or doesn't get smaller)."""
    s = text.strip()
    if not s or s[0] not in "[{":
        return text
    try:
        data = json.loads(s)
    except ValueError:
        return text
    out = json.dumps(_compact(data), separators=(",", ":"), ensure_ascii=False, default=str)
    return out if len(out) < len(text) else text


def page(full: str, result_id: str, offset: int = 0, size: int = PAGE_CHARS) -> str:
    """One page of a long result, ending at a line break where possible, with a note on what's left."""
    offset = max(0, min(offset, len(full)))
    end = min(len(full), offset + size)
    if end < len(full):
        cut = full.rfind("\n", offset + size // 2, end)
        if cut > offset:
            end = cut + 1
    chunk = full[offset:end]
    if end >= len(full) and offset == 0:
        return chunk
    note = (f"\n…[showing characters {offset:,}-{end:,} of {len(full):,}."
            + (f' To continue, call read_more with result_id="{result_id}" and offset={end}.' if end < len(full) else " End of result.")
            + f' To search the whole result, call read_more with result_id="{result_id}" and find="<keyword>".]')
    return chunk + note


def find_in(full: str, term: str, context: int = 400, limit: int = MAX_FIND_CHARS) -> str:
    """Passages of a long result that mention `term`, with surrounding text."""
    hits = [m.start() for m in re.finditer(re.escape(term), full, re.I)]
    if not hits:
        return f'"{term}" doesn\'t appear in this result.'
    spans: list[list[int]] = []
    for h in hits:
        a, b = max(0, h - context), min(len(full), h + len(term) + context)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    parts, used = [], 0
    for a, b in spans:
        if used + (b - a) > limit:
            break
        parts.append(f"[at {a:,}] …{full[a:b]}…")
        used += b - a
    more = len(spans) - len(parts)
    return (f'{len(hits)} match(es) for "{term}":\n\n' + "\n\n".join(parts)
            + (f"\n\n…[{more} more passage(s); narrow the search or read by offset]" if more else ""))
