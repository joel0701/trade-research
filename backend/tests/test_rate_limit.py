"""Rate limits from OpenAI-compatible vendors (e.g. Gemini): wait and retry, or explain."""
import asyncio

import pytest

from app.llm import openai_loop
from app.llm.providers import PROVIDERS
from app.llm.rate_limit import RateLimited, is_daily_or_billing, retry_after


class Err429(Exception):
    status_code = 429

    def __init__(self, msg, body=None):
        super().__init__(msg)
        self.body = body


class Client:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        self.chat = self
        self.completions = self

    async def create(self, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "stream"


class Model:
    model_id = "gemini-4-argon"


def run(client, monkeypatch):
    events, sleeps = [], []

    async def emit(e):
        events.append(e)

    async def sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(openai_loop.asyncio, "sleep", sleep)
    out = asyncio.run(openai_loop._create(client, [], {}, PROVIDERS["google"], Model(), emit, "lead"))
    return out, events, sleeps


def test_per_minute_limit_waits_and_retries(monkeypatch):
    c = Client([Err429("quota", {"error": {"details": [{"retryDelay": "12s"}]}})])
    out, events, sleeps = run(c, monkeypatch)
    assert out == "stream" and c.calls == 2 and sleeps == [13]
    assert events[0]["type"] == "waiting" and "gemini-4-argon" in events[0]["message"]


def test_daily_quota_is_explained_without_waiting(monkeypatch):
    c = Client([Err429("Quota exceeded for metric generate_content_free_tier_requests, limit: 0, PerDay")])
    with pytest.raises(RateLimited, match="Waiting a minute won't help"):
        run(c, monkeypatch)
    assert c.calls == 1


def test_gives_up_after_retries(monkeypatch):
    c = Client([Err429("too many requests")] * 5)
    with pytest.raises(RateLimited, match="even after retries"):
        run(c, monkeypatch)
    assert c.calls == 4


def test_helpers():
    assert retry_after(Err429("x", {"retryDelay": "37s"})) == 37
    assert retry_after(Err429("Please retry in 5.2s")) == 5.2
    assert is_daily_or_billing(Err429("requests per day exceeded"))
    assert not is_daily_or_billing(Err429("requests per minute exceeded"))
