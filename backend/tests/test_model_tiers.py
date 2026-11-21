"""Default models (low / mid / high): set centrally in Settings, used by skills, team and sessions."""
import json

from fastapi.testclient import TestClient

from app.agents import loop
from app.llm import tiers
from app.main import app
from tests.test_orchestrator import FakeClient


def send(c, sid, text):
    with c.stream("POST", f"/api/sessions/{sid}/messages", json={"text": text}) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_defaults_listed_first_and_existing_choices_moved_to_them():
    with TestClient(app) as c:
        models = c.get("/api/models").json()
        assert [m["slug"] for m in models[:3]] == ["default_low", "default_mid", "default_high"]
        assert models[0]["name"] == "Default: low-level (Claude Haiku 5.5)" and models[0]["provider"] == "anthropic"
        assert models[0]["provider_name"] == "Defaults (set in Settings)" and models[0]["available"]
        ids = {m["slug"]: m["id"] for m in models}
        team = {r["slug"]: r for r in c.get("/api/team").json()}
        assert team["lead"]["model_tool_id"] == ids["default_high"]          # was Opus
        assert team["finance_analyst"]["model_tool_id"] == ids["default_mid"]  # was Sonnet
        assert team["sector_mapper"]["model_tool_id"] == ids["default_low"]    # was Haiku


def test_changing_a_default_in_settings_reroutes_everything(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(loop, "client", lambda: fake)
    try:
        with TestClient(app) as c:
            ids = {m["slug"]: m for m in c.get("/api/models").json()}
            # High-level now points at Fable: the Lead (on the high default) uses it, with no other change.
            c.put("/api/profile", json={"model_high_id": ids["fable"]["id"]})
            assert next(m for m in c.get("/api/models").json() if m["slug"] == "default_high")["name"] \
                == "Default: high-level (Claude Fable 5.2)"
            sid = c.post("/api/sessions", json={}).json()["id"]
            send(c, sid, "How is NVDA?")
            lead = next(k for k in fake.calls if "Your team" in k["system"])
            assert lead["model"] == ids["fable"]["config"]["model_id"]
            # A skill on the mid default follows the mid setting.
            skill = c.post("/api/skills", json={"slug": "tier_check", "name": "Tier check", "prompt_template": "Hi",
                                                "params": [], "lead_model_tool_id": ids["default_mid"]["id"]}).json()
            c.put("/api/profile", json={"model_mid_id": ids["haiku"]["id"]})
            r = c.post(f"/api/skills/{skill['id']}/run", json={"params": {}}).json()
            n = len(fake.calls)
            send(c, r["session_id"], r["prompt"])
            assert [k for k in fake.calls[n:] if "Your team" in k["system"]][0]["model"] == ids["haiku"]["config"]["model_id"]
            # A default can't point at another default.
            assert c.put("/api/profile", json={"model_low_id": ids["default_high"]["id"]}).status_code == 422
            c.delete(f"/api/skills/{skill['id']}")
    finally:
        with TestClient(app) as c:
            c.put("/api/profile", json={"model_low_id": None, "model_mid_id": None, "model_high_id": None})
        tiers.set_targets(None, None, None)


def test_retired_gemini_models_are_replaced_but_custom_ids_kept():
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Tool
    from app.seed import ensure_models

    with TestClient(app):
        with Session(engine) as db:
            pro = db.exec(select(Tool).where(Tool.slug == "gemini_pro")).one()
            flash = db.exec(select(Tool).where(Tool.slug == "gemini_flash")).one()
            pro.name, pro.config = "Gemini 2.5 Pro", {"provider": "google", "model_id": "gemini-2.5-pro", "max_tokens": 9000}
            flash.config = {**flash.config, "model_id": "my-own-flash"}
            db.add(pro); db.add(flash); db.commit()
            ensure_models(db)
            db.refresh(pro); db.refresh(flash)
            assert pro.name == "Gemini 4 Argon" and pro.config["model_id"] == "gemini-4-argon"
            assert pro.config["price_input"] == 4
            assert flash.config["model_id"] == "my-own-flash"  # your own choice is left alone
