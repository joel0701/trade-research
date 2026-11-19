"""Smaller tool results without losing information: compaction, paging with read_more, sizes."""
import asyncio
import json

from app.models import Tool
from app.tools import compact, python_tools
from app.tools.registry import Toolbox
from app.tools.sec_edgar import filing_sections


def test_json_rows_become_a_table_without_losing_values():
    rows = [{"date": f"2026-11-{d:02d}", "open": 100.123456789, "close": 101.5, "volume": 1200000.0, "note": None}
            for d in range(1, 31)]
    raw = json.dumps({"ticker": "NVDA", "bars": rows, "empty": ""}, indent=2)
    out = compact.compact_text(raw)
    data = json.loads(out)
    assert data["bars"]["columns"] == ["date", "open", "close", "volume"]  # all-empty "note" dropped
    assert data["bars"]["rows"][0] == ["2026-11-01", 100.1235, 101.5, 1200000] and len(data["bars"]["rows"]) == 30
    assert "empty" not in data and len(out) < len(raw) * 0.4
    assert compact._num(0.0123456) == 0.01235 and compact._num(float("nan")) is None


def test_non_json_and_dissimilar_lists_are_left_alone():
    assert compact.compact_text("Plain text result.") == "Plain text result."
    mixed = json.dumps([{"a": 1}, {"b": 2}, {"c": 3}])
    assert json.loads(compact.compact_text(mixed)) == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_long_results_are_paged_and_searchable_with_read_more(monkeypatch):
    lines = [f"Line {i}: revenue grew in segment {i}." for i in range(2000)]
    lines[1500] = "Line 1500: GUIDANCE raised for fiscal 2027."
    text = "\n".join(lines)

    async def big(**kw):
        return text
    monkeypatch.setitem(python_tools.PYTHON_TOOLS, "big_doc",
                        python_tools.PythonTool("A long document.", {"type": "object", "properties": {}}, big))
    tool = Tool(id=1, slug="big_doc", name="Big", kind="python", config={"function": "big_doc"})

    async def go():
        async with Toolbox([tool], {}) as tb:
            assert "read_more" in [d["name"] for d in tb.definitions]
            meta = {}
            first, err = await tb.execute("big_doc", {}, meta)
            assert not err and meta["raw_chars"] == len(text) and meta["chars"] == len(first) < compact.PAGE_CHARS + 400
            assert first.endswith('find="<keyword>".]') and 'result_id="r1"' in first and first.startswith("Line 0:")
            offset = int(first.split("offset=")[1].split(".")[0])
            second, _ = await tb.execute("read_more", {"result_id": "r1", "offset": offset})
            assert second.startswith(text[offset:offset + 20])  # continues exactly where page 1 ended
            found, _ = await tb.execute("read_more", {"result_id": "r1", "find": "guidance"})
            assert "1 match(es)" in found and "GUIDANCE raised for fiscal 2027" in found
            missing, _ = await tb.execute("read_more", {"result_id": "nope"})
            assert missing.startswith("Unknown result_id")
    asyncio.run(go())


def test_filing_sections_listed():
    text = "PART I\nItem 1. Business\nWe make chips.\nItem 1A. Risk Factors\nRisks...\nPART II\nItem 7. Management's Discussion"
    assert filing_sections(text) == ["PART I", "Item 1. Business", "Item 1A. Risk Factors", "PART II",
                                     "Item 7. Management's Discussion"]
