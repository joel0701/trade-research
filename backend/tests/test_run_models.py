"""A scanner's or skill's model runs only its first answer; follow-ups use the Research default."""
import json

from fastapi.testclient import TestClient

from app.agents import loop
from app.main import app
from app.tools import ibkr_gateway
from tests.test_orchestrator import FakeClient


def send(c, sid, body):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json=body) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def lead_models(fake):
    return [k["model"] for k in fake.calls if "Your team" in k["system"]]


def test_skill_model_for_first_answer_only(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        tools = {t["slug"]: t for t in c.get("/api/tools").json()}
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        r = c.post(f"/api/skills/{skill['id']}/run",
                   json={"params": {"ticker": "AAPL"}, "lead_model_tool_id": tools["haiku"]["id"]}).json()
        send(c, r["session_id"], {"text": r["prompt"]})
        n = len(lead_models(fake))
        send(c, r["session_id"], {"text": "And the risks?"})
        assert set(lead_models(fake)[:n]) == {tools["haiku"]["config"]["model_id"]}
        assert set(lead_models(fake)[n:]) == {tools["opus"]["config"]["model_id"]}


def test_skill_sources_limit_its_run_but_not_follow_ups(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        t = {x["slug"]: x for x in c.get("/api/tools").json()}
        skill = c.post("/api/skills", json={"slug": "short_check", "name": "Short check",
                                            "prompt_template": "Short interest for {ticker}?",
                                            "params": [{"name": "ticker", "label": "Ticker"}],
                                            "tool_ids": [t["finra_short_volume"]["id"]],
                                            "lead_model_tool_id": t["haiku"]["id"]}).json()
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "CRWV"}}).json()
        send(c, r["session_id"], {"text": r["prompt"]})
        leads = [k for k in fake.calls if "Your team" in k["system"]]
        first = {x["name"] for x in leads[0]["tools"]} - {"read_more"}
        assert first == {"consult_technical_analyst"}  # only the role holding the skill's source
        n = len(leads)
        send(c, r["session_id"], {"text": "And the fundamentals?"})
        follow = [k for k in fake.calls if "Your team" in k["system"]][n:]
        assert {"consult_finance_analyst", "consult_news_analyst"} <= {x["name"] for x in follow[0]["tools"]}
        assert follow[0]["model"] == t["opus"]["config"]["model_id"]


def test_run_kind_for_skill_scanner_and_forks(monkeypatch):
    from app.screeners import engine

    async def fake_scan(**kw):
        return [{"rank": 1, "ticker": "CRWV"}]
    monkeypatch.setattr(ibkr_gateway, "run_scan", fake_scan)
    monkeypatch.setattr(engine, "fetch_snapshots", lambda t: {})
    with TestClient(app) as c:
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "ibkr_top_gainers")
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        chat = c.get(f"/api/sessions/{r['session_id']}").json()
        assert chat["run_kind"] == "scanner" and chat["run_name"] == "IBKR: top % gainers"
        first = c.get(f"/api/sessions/{r['session_id']}").json()["messages"][1]["id"]
        fork = c.post(f"/api/sessions/{r['session_id']}/fork", json={"message_id": first}).json()
        assert c.get(f"/api/sessions/{fork['id']}").json()["run_name"] == "IBKR: top % gainers"
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        r2 = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "AAPL"}}).json()
        chat = c.get(f"/api/sessions/{r2['session_id']}").json()
        assert chat["run_kind"] == "skill" and chat["run_name"] == skill["name"]
        plain = c.post("/api/sessions", json={}).json()
        assert c.get(f"/api/sessions/{plain['id']}").json()["run_kind"] is None


def test_mock_answer_includes_next_steps(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
    with TestClient(app) as c:
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "NVDA"}}).json()
        send(c, r["session_id"], {"text": r["prompt"]})
        last = c.get(f"/api/sessions/{r['session_id']}").json()["messages"][-1]["text"]
        assert "### Suggested next steps\n- What news is driving the price action in NVDA today?" in last


def fake_ibkr(monkeypatch, tickers=("CRWV", "NVDA")):
    from app.screeners import engine

    async def fake_scan(**kw):
        return [{"rank": i + 1, "ticker": t} for i, t in enumerate(tickers)]
    monkeypatch.setattr(ibkr_gateway, "run_scan", fake_scan)
    monkeypatch.setattr(engine, "fetch_snapshots", lambda t: {})


def test_scanner_run_shows_results_only_then_questions_use_the_default(monkeypatch):
    """A scanner only scans: no LLM runs until you ask, and then with the Research default and full team."""
    fake_ibkr(monkeypatch)
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        tools = {t["slug"]: t for t in c.get("/api/tools").json()}
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "ibkr_top_gainers")
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        assert "prompt" not in r and fake.calls == []  # nothing ran, nothing cost anything
        chat = c.get(f"/api/sessions/{r['session_id']}").json()
        assert [m["role"] for m in chat["messages"]] == ["user", "assistant"] and not chat["running"]
        assert chat["next_turn_model_tool_id"] is None and chat["next_turn_note"] is None
        res = chat["messages"][1]["text"]
        assert "### Suggested next steps\n- What news is driving today's moves in CRWV and NVDA?" in res
        send(c, r["session_id"], {"text": "What news is driving today's moves in CRWV and NVDA?"})
        lead = [k for k in fake.calls if "Your team" in k["system"]][0]
        assert lead["model"] == tools["opus"]["config"]["model_id"] and "Suggested next steps" in lead["system"]
        assert {"consult_finance_analyst", "consult_news_analyst"} <= {t["name"] for t in lead["tools"]}


def test_skill_on_scanner_scan_then_skill_model(monkeypatch):
    """Skill built on a scanner: scan (no LLM) → the skill's prompt with the skill's model → follow-ups on the default."""
    fake_ibkr(monkeypatch, ("CRWV", "NVDA", "HIMS"))
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    heading = "### Suggested next steps"
    with TestClient(app) as c:
        tools = {t["slug"]: t for t in c.get("/api/tools").json()}
        mid = {k: tools[k]["config"]["model_id"] for k in ("haiku", "opus")}
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "ibkr_top_gainers")
        skill = c.post("/api/skills", json={
            "slug": "gainer_picks2", "name": "Gainer picks", "prompt_template": "Pick the best {style} setups.",
            "params": [{"name": "style", "label": "Style", "default": "swing"}],
            "screener_id": sc["id"], "lead_model_tool_id": tools["haiku"]["id"]}).json()
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"style": "swing"}}).json()
        assert r["hidden"] and r["prompt"].startswith("Pick the best **swing** setups.\n\nBase this on the scanner results above.")
        assert "don't research, add or recommend other tickers" in r["prompt"]
        msgs = c.get(f"/api/sessions/{r['session_id']}").json()["messages"]
        assert msgs[0]["text"].startswith("Pick the best **swing** setups.\n\n**Using scanner IBKR: top % gainers**")
        assert "- Skill evaluation: **Claude Haiku 5.5**" in msgs[0]["text"] and "Scanner analysis" not in msgs[0]["text"]
        assert heading not in msgs[1]["text"]  # the skill gives the suggestions, not the scan

        ev = send(c, r["session_id"], {"text": r["prompt"], "hidden": True})
        assert [e["type"] for e in ev].count("done") == 1  # one stage: the skill
        leads = [k for k in fake.calls if "Your team" in k["system"]]
        assert {k["model"] for k in leads} == {mid["haiku"]} and all(heading in k["system"] for k in leads)
        n = len(leads)
        send(c, r["session_id"], {"text": "Which one first?"})
        follow = [k for k in fake.calls if "Your team" in k["system"]][n:]
        assert {k["model"] for k in follow} == {mid["opus"]} and all(heading in k["system"] for k in follow)

        c.delete(f"/api/screeners/{sc['id']}")  # deleting the scanner turns the skill back into a plain skill
        assert next(s for s in c.get("/api/skills").json() if s["id"] == skill["id"])["screener_id"] is None
        from sqlmodel import Session, select
        from app.db import engine as db_engine
        from app.models import DismissedDefault
        from app.seed import ensure_scanners
        with Session(db_engine) as db:  # restore the default scanner for other tests
            for d in db.exec(select(DismissedDefault).where(DismissedDefault.slug == "ibkr_top_gainers")).all():
                db.delete(d)
            db.commit()
            ensure_scanners(db)


def test_scan_shows_session_and_warns(monkeypatch):
    from datetime import datetime
    from app.api import launch
    fake_ibkr(monkeypatch, ("SOXL", "IBIT"))
    monkeypatch.setattr(launch, "now_et", lambda: datetime(2026, 11, 18, 20, 54, tzinfo=launch.ET))
    with TestClient(app) as c:
        sc = next(s for s in c.get("/api/screeners").json() if s["slug"] == "ibkr_top_gainers")
        r = c.post(f"/api/screeners/{sc['id']}/run").json()
        msgs = c.get(f"/api/sessions/{r['session_id']}").json()["messages"]
        assert "- Market session: **closed** (Outside trading hours" in msgs[0]["text"]
        assert "⚠ Only 2 of 25 requested results (normal outside regular trading hours)." in msgs[1]["text"]
        assert "from Yahoo's daily bars as of the last close" in msgs[1]["text"]
    assert launch.market_session(datetime(2026, 11, 18, 10, 0, tzinfo=launch.ET))[0] == "open"
    assert launch.market_session(datetime(2026, 11, 21, 12, 0, tzinfo=launch.ET))[0] == "closed (weekend)"
    assert launch.market_session(datetime(2026, 11, 18, 17, 0, tzinfo=launch.ET))[0] == "after hours"


def test_every_answer_gets_next_steps(monkeypatch):
    """Skill runs and follow-up questions alike end with suggested next steps."""
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "AAPL"}}).json()
        send(c, r["session_id"], {"text": r["prompt"]})
        send(c, r["session_id"], {"text": "And the risks?"})
        leads = [k for k in fake.calls if "Your team" in k["system"]]
        assert len(leads) >= 2 and all("### Suggested next steps" in k["system"] for k in leads)
        assert all(k["system"].count("### Suggested next steps") == 1 for k in leads)  # asked once, not twice


def test_lead_consults_each_specialist_at_most_twice_per_question(monkeypatch):
    """A Lead that asks the same specialist once per ticker is capped; the extra consult isn't run."""
    from tests.test_orchestrator import msg

    class PerTickerLead(FakeClient):
        def respond(self, kw):
            is_lead = "Your team" in kw["system"]
            last = kw["messages"][-1]["content"]
            got_results = isinstance(last, list) and any(b.get("type") == "tool_result" for b in last)
            if is_lead and not got_results:
                return msg([{"type": "tool_use", "id": f"tu_{t}", "name": "consult_news_analyst",
                             "input": {"question": f"News on {t}?"}} for t in ("COLM", "CRI", "CGGR")], "tool_use")
            if is_lead:
                self.results = last
                return msg([{"type": "text", "text": "Done."}], "end_turn")
            return msg([{"type": "text", "text": "Some news."}], "end_turn", "claude-sonnet-5-5")

    fake = PerTickerLead()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        send(c, sid, {"text": "What news is driving COLM, CRI and CGGR?"})
        lead_sys = next(k["system"] for k in fake.calls if "Your team" in k["system"])
        assert "Ask each specialist once per question" in lead_sys and "2 consult(s) per specialist, 5 in total" in lead_sys
        specialist_calls = [k for k in fake.calls if "Your team" not in k["system"]]
        assert len(specialist_calls) == 2  # the third consult wasn't run
        texts = [b["content"] if isinstance(b["content"], str) else b["content"][0]["text"] for b in fake.results]
        assert sum("Not run: you've already consulted the News Analyst 2 time(s)" in t for t in texts) == 1
        runs = [r for r in c.get(f"/api/sessions/{sid}/runs").json() if r["role_slug"] == "news_analyst"]
        assert len(runs) == 2


def test_strict_team_mode_on_a_local_lead(monkeypatch):
    """Local Leads get the default limits; with strict team mode on: 1 per specialist, 3 in total, concise reports."""
    import httpx
    from types import SimpleNamespace as NS
    from app.llm import openai_loop, providers
    from tests.test_local_models import fake_get
    from tests.test_vendors import chunk

    asks = [("news_analyst", "COLM"), ("news_analyst", "CRI"), ("technical_analyst", "all"),
            ("finance_analyst", "all"), ("sector_mapper", "all")]
    seen = {}

    class Completions:
        async def create(self, **kw):
            seen.setdefault("calls", []).append(kw)
            has_results = any(m["role"] == "tool" for m in kw["messages"])

            async def stream():
                if not has_results:
                    for i, (role, t) in enumerate(asks):
                        yield chunk(tool=(i, f"call_{i}", f"consult_{role}", json.dumps({"question": f"News on {t}?"})))
                    yield chunk(finish="tool_calls")
                else:
                    seen["results"] = [m["content"] for m in kw["messages"] if m["role"] == "tool"]
                    yield chunk(content="Summary.")
                    yield chunk(finish="stop")
            return stream()

    monkeypatch.setattr(openai_loop, "openai_client", lambda provider: NS(chat=NS(completions=Completions())))
    providers._reach_cache.clear(); providers._state_cache.clear()
    monkeypatch.setattr(httpx, "get", fake_get({"http://localhost:1234/v1": ["qwen/qwen3.8-27b"]}))
    fake = FakeClient()  # specialists stay on Claude (faked)
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        tool = c.post("/api/local-models", json={"provider": "lmstudio", "model_id": "qwen/qwen3.8-27b"}).json()
        # Default: the same limits as any Lead (all five asks are within 2 per specialist / 5 in total).
        sid = c.post("/api/sessions", json={"lead_model_tool_id": tool["id"]}).json()["id"]
        send(c, sid, {"text": "What news is driving COLM, CRI and CGGR?"})
        assert "2 consult(s) per specialist, 5 in total" in seen["calls"][0]["messages"][0]["content"]
        assert len([r for r in c.get(f"/api/sessions/{sid}/runs").json() if r["parent_run_id"]]) == 5
        # Strict team mode on for this model.
        c.put(f"/api/tools/{tool['id']}", json={"config": {**tool["config"], "strict_team": True}})
        seen.clear()
        sid = c.post("/api/sessions", json={"lead_model_tool_id": tool["id"]}).json()["id"]
        send(c, sid, {"text": "What news is driving COLM, CRI and CGGR?"})
        system = seen["calls"][0]["messages"][0]["content"]
        assert "1 consult(s) per specialist, 3 in total" in system and "Strict team mode" in system
        ran = [r["role_slug"] for r in c.get(f"/api/sessions/{sid}/runs").json() if r["parent_run_id"]]
        assert sorted(ran) == ["finance_analyst", "news_analyst", "technical_analyst"]  # 2nd News + Sector Mapper skipped
        assert sum(r.startswith("Not run:") for r in seen["results"]) == 2
        questions = [r["input"] for r in c.get(f"/api/sessions/{sid}/runs").json() if r["parent_run_id"]]
        assert all("Reply concisely" in q for q in questions)
        c.delete(f"/api/tools/{tool['id']}")
    providers._reach_cache.clear(); providers._state_cache.clear()
