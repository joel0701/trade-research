import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Json } from "../api";
import { Field, JsonField, Toggle, useCrud } from "../admin";
import { Dialog, errorText, LeadModelSelect, Section, TwoPane, useDraft } from "../TwoPane";
import { Chip, Database, Gear, Prompt, Radar, Sparkles } from "../icons";
import SourcePicker, { DataNotes } from "../SourcePicker";

const BLANK = { slug: "", name: "New skill", description: "", prompt_template: "", lead_model_tool_id: null,
                params: [{ name: "ticker", label: "Ticker", default: "" }], tool_ids: [], screener_id: null, enabled: true };

function RunSkillDialog({ skill, models, leadDefault, scanners, onClose }: {
  skill: Json; models: Json[]; leadDefault: string; scanners: Json[]; onClose: () => void;
}) {
  const scanner = scanners.find((x) => x.id === skill.screener_id);
  const modelName = (id: number | null | undefined) => models.find((m) => m.id === id)?.name ?? `Research default (${leadDefault || "Lead Analyst's model"})`;
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries((skill.params ?? []).map((p: Json) => [p.name, p.default ?? ""])));
  const [model, setModel] = useState<number | null>(skill.lead_model_tool_id ?? null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const missing = (skill.params ?? []).filter((p: Json) => !String(values[p.name] ?? "").trim());

  async function run() {
    setRunning(true); setError("");
    try {
      const r = await api.post(`/skills/${skill.id}/run`, { params: values, lead_model_tool_id: model });
      navigate(`/research/${r.session_id}`, { state: { autoSend: r.prompt, hidden: !!r.hidden } });
    } catch (e: any) { setError(errorText(e)); setRunning(false); }
  }

  return (
    <Dialog title={`Run: ${skill.name}`} onClose={onClose} tone="blue" icon={<Sparkles />}
            subtitle={skill.description || (scanner ? `Evaluates ${scanner.name} results` : "Skill")}>
      {scanner && (
        <ol className="stages">
          <li className="tone-green"><span>Scan (no AI)</span><strong>{scanner.name}</strong></li>
          <li className="tone-blue"><span>Skill evaluation</span><strong>model below</strong></li>
          <li className="tone-purple"><span>Follow-ups</span><strong>{modelName(null)}</strong></li>
        </ol>
      )}
      <form onSubmit={(e) => { e.preventDefault(); if (!missing.length) run(); }}>
        {(skill.params ?? []).map((p: Json, i: number) => (
          <div key={p.name}>
            <label>{p.label ?? p.name}</label>
            <input autoFocus={i === 0} value={values[p.name] ?? ""} placeholder={p.default || ""}
                   onChange={(e) => setValues({ ...values, [p.name]: e.target.value })} />
          </div>
        ))}
        <LeadModelSelect models={models} value={model} onChange={setModel} label="Model for this run"
                         hint="follow-up questions use the Research default" defaultLabel={`Research default (${leadDefault || "Lead Analyst's model"})`} />
        <DataNotes ids={skill.tool_ids ?? []} />
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button className="primary" disabled={running || missing.length > 0}>
            {running ? "Starting…" : "Run"}
          </button>
          <button type="button" onClick={onClose}>Cancel</button>
          {missing.length > 0 && <span className="muted small">Fill in: {missing.map((p: Json) => p.label ?? p.name).join(", ")}</span>}
        </div>
      </form>
    </Dialog>
  );
}

export default function Skills() {
  const crud = useCrud("/skills");
  const { draft, set } = useDraft(crud.selected);
  const [models, setModels] = useState<Json[]>([]);
  const [leadDefault, setLeadDefault] = useState("");
  const [running, setRunning] = useState(false);
  const [scanners, setScanners] = useState<Json[]>([]);

  useEffect(() => {
    api.get("/models").then(setModels);
    api.get("/screeners").then(setScanners);
    api.get("/team").then((rs) => {
      const id = rs.find((r: Json) => r.is_lead)?.model_tool_id;
      api.get("/models").then((ms) => setLeadDefault(ms.find((m: Json) => m.id === id)?.name ?? ""));
    });
  }, []);
  // Open the first skill when the page loads.
  useEffect(() => { if (!crud.selected && crud.items.length) crud.setSelected(crud.items[0]); }, [crud.items]);

  return (
    <TwoPane title="Skills" panelKey="skills" items={crud.items} selectedId={crud.selected?.id}
             onSelect={crud.setSelected} onNew={() => crud.setSelected(structuredClone(BLANK))}>
      {!draft ? <p className="muted">Select a skill, or create one with + New.</p> : (
        <div className="detail">
          <div className="detail-head">
            <div>
              <h2>{draft.name || "Untitled skill"}</h2>
              {draft.description && <p className="muted">{draft.description}</p>}
            </div>
            <button className="primary run-btn" disabled={!draft.id || !draft.enabled}
                    title={!draft.id ? "Save the skill first" : !draft.enabled ? "Enable the skill to run it" : "Run this skill"}
                    onClick={() => setRunning(true)}>▶ Run</button>
          </div>
          <div className="cfg-stack">
            <Section tone="blue" icon={<Gear />} title="Setup">
              <Field label="Name" value={draft.name} onChange={(name) => set({ name })} />
              <div className="grid-2">
                <div><Field label="Slug (unique id)" value={draft.slug} onChange={(slug) => set({ slug })} /></div>
                <div><Field label="Description" value={draft.description} onChange={(description) => set({ description })} /></div>
              </div>
              <Toggle label="Enabled" value={draft.enabled} onChange={(enabled) => set({ enabled })} />
            </Section>
            <Section tone="amber" icon={<Radar />} title="Scanner input" hint="optional">
              <select value={draft.screener_id ?? ""} onChange={(e) => set({ screener_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">None: the prompt runs on its own</option>
                {scanners.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
              <p className="muted small">{draft.screener_id
                ? "Running the skill runs this scan first (no AI model). The prompt below then evaluates the results with the skill's model."
                : "Pick a scanner to have this skill evaluate its results."}</p>
            </Section>
            <Section tone="purple" icon={<Prompt />} title="Prompt"
                     hint={draft.screener_id ? "how to evaluate the scanner's results" : "use {field} placeholders"}>
              <Field label="Prompt template" value={draft.prompt_template}
                     onChange={(prompt_template) => set({ prompt_template })} multiline rows={7} />
              <JsonField label='Fields asked for when running: [{"name", "label", "default"}]' value={draft.params}
                         onChange={(params) => set({ params })} rows={6} />
            </Section>
            <Section tone="green" icon={<Chip />} title="Model" hint="follow-up questions use the Research default">
              <LeadModelSelect models={models} value={draft.lead_model_tool_id ?? null}
                               onChange={(lead_model_tool_id) => set({ lead_model_tool_id })}
                               label="Model the skill runs with"
                               defaultLabel={`Research default (${leadDefault || "Lead Analyst's model"})`} />
            </Section>
            <Section tone="teal" icon={<Database />} title="Data sources" hint="for the skill's run only">
              <p className="muted small">
                The skill's run uses only these sources, and their notes (limits such as a fixed lookback) are added to the
                prompt as a callout. Don't add fields for things a source fixes. Follow-up questions use the full team.
                {(draft.tool_ids ?? []).length === 0 && <strong> None chosen: the full team's sources are used.</strong>}
              </p>
              <SourcePicker value={draft.tool_ids ?? []} onChange={(tool_ids) => set({ tool_ids })} />
            </Section>
            <div className="cfg-actions">
              {crud.error && <p className="error">{crud.error}</p>}
              <button className="primary" onClick={() => crud.save(draft)}>Save</button>
              {draft.id && <button onClick={() => crud.remove(draft)}>Delete</button>}
            </div>
          </div>
        </div>
      )}
      {running && draft?.id && (
        <RunSkillDialog skill={crud.items.find((s) => s.id === draft.id) ?? draft} models={models}
                        leadDefault={leadDefault} scanners={scanners} onClose={() => setRunning(false)} />
      )}
    </TwoPane>
  );
}
