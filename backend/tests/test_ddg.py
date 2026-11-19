"""DuckDuckGo search: empty results aren't errors, rate limits retry, results are trimmed."""
import asyncio
import json

import ddgs
from ddgs.exceptions import DDGSException, RatelimitException

from app.tools import python_tools as pt


class FakeDDGS:
    script: list = []
    calls = 0

    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text(self, query, max_results=8):
        FakeDDGS.calls += 1
        step = FakeDDGS.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    news = text


def run(monkeypatch, script, **kw):
    FakeDDGS.script, FakeDDGS.calls = list(script), 0
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    monkeypatch.setattr(pt, "DDG_MIN_INTERVAL", 0)
    monkeypatch.setattr(pt.time, "sleep", lambda s: None)
    return asyncio.run(pt.ddg_search("CRWV news", **kw))


def test_no_results_is_not_an_error(monkeypatch):
    assert run(monkeypatch, [DDGSException("No results found.")]) == "No results."


def test_rate_limit_retries_then_succeeds(monkeypatch):
    out = run(monkeypatch, [RatelimitException("202 Ratelimit"),
                            [{"title": "CRWV jumps", "href": "https://x.test/a", "body": "Shares rose", "image": "big.png"}]])
    assert FakeDDGS.calls == 2 and json.loads(out) == [{"title": "CRWV jumps", "href": "https://x.test/a", "body": "Shares rose"}]


def test_rate_limit_gives_up_with_a_clear_message(monkeypatch):
    out = run(monkeypatch, [RatelimitException("202")] * 3)
    assert FakeDDGS.calls == 3 and out.startswith("DuckDuckGo search rate-limited after 3 tries")
