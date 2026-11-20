/** Drafts a skill from the conversation, lets the user edit it, and saves it. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import SourcePicker from "./SourcePicker";
import { api, Json } from "./api";
import { Field, JsonField } from "./admin";
import { ModelInfo, modelLabel } from "./models";

export default function SkillDraftDialog({ sessionId, models, onClose, onSaved }: {
  sessionId: number; models: ModelInfo[]; onClose: () => void; onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Json | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<Json | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.post(`/sessions/${sessionId}/skill-draft`)
      .then((d) => { if (!cancelled) setDraft(d); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  async function save() {
    if (!draft) return;
    setSaving(true); setError("");
    try {
      const { drafted_with: _ignored, ...body } = draft;
      setSaved(await api.post("/skills", body));
      onSaved();
    } catch (e: any) { setError(e.message); } finally { setSaving(false); }
  }

  const set = (patch: Json) => setDraft({ ...draft!, ...patch });

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Save as skill">
        <div className="modal-head">
          <strong>Save as skill</strong>
          <button className="link" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {saved ? (
          <div>
            <p>Saved <strong>{saved.name}</strong>. Run it from <strong>Skills</strong> in the composer or on
              the <Link to="/skills" onClick={onClose}>Skills page</Link>.</p>
            <div className="actions"><button className="primary" onClick={onClose}>Done</button></div>
          </div>
        ) : !draft ? (
          error ? <p className="error">{error}</p>
                : <p className="muted"><span className="spinner small" /> Drafting a reusable skill from this conversation…</p>
        ) : (
          <>
            <p className="muted small">Drafted with {draft.drafted_with}. Review and edit before saving.
              Placeholders like {"{ticker}"} become fields you fill in when you run it.</p>
            <Field label="Name" value={draft.name} onChange={(name) => set({ name })} />
            <Field label="Description" value={draft.description} onChange={(description) => set({ description })} />
            <Field label="Prompt template" value={draft.prompt_template}
                   onChange={(prompt_template) => set({ prompt_template })} multiline rows={7} />
            <JsonField label='Fields: [{"name", "label", "default"}]' value={draft.params}
                       onChange={(params) => set({ params })} rows={5} />
            <label>Data sources this skill uses</label>
            <p className="muted small">Pre-selected from the sources this conversation used. Their notes are added to the
              prompt when it runs. None = the full team's sources. Follow-up questions always use the full team.</p>
            <SourcePicker value={draft.tool_ids ?? []} onChange={(tool_ids) => set({ tool_ids })} />
            <label>Model the skill runs with <span className="muted small">· follow-up questions use the Research default</span></label>
            <select value={draft.lead_model_tool_id ?? ""}
                    onChange={(e) => set({ lead_model_tool_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Research default</option>
              {models.filter((m) => m.available).map((m) => (
                <option key={m.id} value={m.id}>{modelLabel(m)}</option>
              ))}
            </select>
            {error && <p className="error">{error}</p>}
            <div className="actions">
              <button className="primary" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save skill"}</button>
              <button onClick={onClose}>Cancel</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
