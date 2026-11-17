"""Turn a research conversation into a reusable skill (a prompt template with parameters).

An LLM drafts the skill; the result is then normalized so it is always valid: a unique slug,
parameter names that are identifiers, and a template whose {placeholders} match the
parameters exactly. The user reviews and edits the draft before saving it.
"""
import json
import re
from typing import Any

from sqlmodel import Session, select

from app.agents.loop import ModelConfig, client
from app.llm.mock_loop import mock_mode, mock_skill_draft
from app.llm.providers import model_available, provider_of
from app.models import Skill, Tool

PREFERRED = ["sonnet", "opus", "haiku", "fable"]  # good, inexpensive drafting first
MAX_CHARS_PER_MESSAGE = 3000
MAX_TRANSCRIPT_CHARS = 30000

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "prompt_template": {"type": "string"},
        "params": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "label": {"type": "string"}, "default": {"type": "string"}},
            "required": ["name", "label", "default"], "additionalProperties": False}},
    },
    "required": ["name", "description", "prompt_template", "params"],
    "additionalProperties": False,
}

SYSTEM = """You turn a stock and options research conversation into a reusable "skill": a prompt
template the user can run again on different inputs.

- Capture what the user was really trying to learn, including follow-up questions that refined
  the request, as ONE clear, complete instruction for a research team.
- Replace specifics that would change between runs (tickers, sectors, dates, horizons, price
  levels, strategy views) with placeholders like {ticker}. Keep everything else concrete.
- Use few parameters (usually 1-3). Parameter names are lowercase identifiers. Give each a short
  label and a sensible default ("" if none, e.g. for a ticker).
- name: 2-5 words, Title Case. description: one sentence on what the skill produces.
- Do not include the answers from the conversation in the template."""


class DraftError(Exception):
    pass


def pick_model(db: Session) -> ModelConfig:
    if mock_mode():
        return ModelConfig(model_id="mock", provider="mock", effort=None)
    models = {t.slug: t for t in db.exec(select(Tool).where(Tool.kind == "model", Tool.enabled == True)).all()}  # noqa: E712
    ordered = [models[s] for s in PREFERRED if s in models] + [t for s, t in models.items() if s not in PREFERRED]
    for t in ordered:
        if model_available(t.config):
            return ModelConfig.from_tool_config(t.config)
    raise DraftError("No model has an API key configured, so a skill can't be drafted.")


def transcript(convo: list[dict[str, Any]]) -> str:
    parts = []
    for m in convo:
        who = "User" if m["role"] == "user" else "Analyst"
        text = m["text"].strip()
        if len(text) > MAX_CHARS_PER_MESSAGE:
            text = text[:MAX_CHARS_PER_MESSAGE] + " …[truncated]"
        files = ", ".join(a["filename"] for a in m.get("attachments") or [])
        parts.append(f"{who}{' (attached: ' + files + ')' if files else ''}: {text}")
    out = "\n\n".join(parts)
    return out[-MAX_TRANSCRIPT_CHARS:]


async def complete_json(model: ModelConfig, system: str, user: str) -> dict[str, Any]:
    """Ask a model for a JSON object matching SCHEMA."""
    provider = provider_of({"provider": model.provider})
    if provider.api == "mock":
        return mock_skill_draft(user)
    if provider.api == "anthropic":
        resp = await client().messages.create(
            model=model.model_id, max_tokens=4000, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            raise DraftError("The model declined to draft this skill.")
        text = "".join(b.text for b in resp.content if b.type == "text")
    else:
        from app.llm.openai_loop import openai_client

        oc = openai_client(provider)
        msgs = [{"role": "system", "content": system + "\n\nReply with only a JSON object with keys: "
                 "name, description, prompt_template, params (list of {name, label, default})."},
                {"role": "user", "content": user}]
        try:
            resp = await oc.chat.completions.create(model=model.model_id, messages=msgs,
                                                    response_format={"type": "json_object"})
        except Exception:  # some vendors don't support response_format
            resp = await oc.chat.completions.create(model=model.model_id, messages=msgs)
        text = resp.choices[0].message.content or ""
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group(0) if match else text)
    except json.JSONDecodeError as e:
        raise DraftError("The model's draft wasn't valid JSON; please try again.") from e


PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _ident(s: str) -> str:
    s = re.sub(r"[^a-z0-9_]", "_", s.strip().lower()).strip("_")
    return ("p_" + s) if s[:1].isdigit() else (s or "param")


def normalize(draft: dict[str, Any], db: Session) -> dict[str, Any]:
    name = (str(draft.get("name") or "").strip() or "New skill")[:80]
    template = str(draft.get("prompt_template") or "").strip()
    if not template:
        raise DraftError("The draft had no prompt; please try again.")

    # Rename params to identifiers, and rewrite their placeholders to match.
    params, seen = [], set()
    for p in draft.get("params") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        old, new = str(p["name"]), _ident(str(p["name"]))
        if new in seen:
            continue
        seen.add(new)
        template = template.replace("{" + old + "}", "{" + new + "}")
        params.append({"name": new, "label": str(p.get("label") or new.replace("_", " ").title()),
                       "default": str(p.get("default") or "")})

    # Escape stray braces so str.format() only sees real placeholders.
    pieces, last = [], 0
    for m in PLACEHOLDER.finditer(template):
        pieces.append(template[last:m.start()].replace("{", "{{").replace("}", "}}"))
        pieces.append(m.group(0))
        last = m.end()
    pieces.append(template[last:].replace("{", "{{").replace("}", "}}"))
    template = "".join(pieces)

    # Placeholders and params must match exactly.
    used = list(dict.fromkeys(PLACEHOLDER.findall(template)))
    params = [p for p in params if p["name"] in used]
    for n in used:
        if n not in {p["name"] for p in params}:
            params.append({"name": n, "label": n.replace("_", " ").title(), "default": ""})

    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "skill"
    taken = {s.slug for s in db.exec(select(Skill)).all()}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1

    return {"slug": slug, "name": name, "description": str(draft.get("description") or "").strip()[:300],
            "prompt_template": template, "params": params, "enabled": True}


async def draft_skill(db: Session, convo: list[dict[str, Any]]) -> dict[str, Any]:
    model = pick_model(db)
    raw = await complete_json(model, SYSTEM, "Conversation:\n\n" + transcript(convo))
    return {**normalize(raw, db), "drafted_with": model.model_id}
