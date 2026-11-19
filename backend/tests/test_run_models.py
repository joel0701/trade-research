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
        first = {x["name"] for x in leads[0]["tools"]}
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
        assert lead["model"] == tools["opus"]["config"]["model_id"] and "Suggested next steps" not in lead["system"]
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
        assert {k["model"] for k in follow} == {mid["opus"]} and not any(heading in k["system"] for k in follow)

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


def test_plain_skill_answer_gets_next_steps(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    with TestClient(app) as c:
        skill = next(s for s in c.get("/api/skills").json() if s["slug"] == "full_analysis")
        r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {"ticker": "AAPL"}}).json()
        send(c, r["session_id"], {"text": r["prompt"]})
        n = len(fake.calls)
        assert any("### Suggested next steps" in k["system"] for k in fake.calls if "Your team" in k["system"])
        send(c, r["session_id"], {"text": "And the risks?"})
        assert not any("### Suggested next steps" in k["system"] for k in fake.calls[n:] if "Your team" in k["system"])

