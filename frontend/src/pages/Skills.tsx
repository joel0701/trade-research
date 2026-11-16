import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Json } from "../api";
import { Field, JsonField, MasterDetail, Toggle, useCrud } from "../admin";

function ModelSelect({ models, value, onChange, defaultLabel }:
  { models: Json[]; value: number | null; onChange: (v: number | null) => void; defaultLabel: string }) {
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{defaultLabel}</option>
      {models.filter((m) => m.available).map((m) => <option key={m.id} value={m.id}>{m.provider_name}: {m.name}</option>)}
    </select>
  );
}

function useModels() {
  const [models, setModels] = useState<Json[]>([]);
  const [leadDefault, setLeadDefault] = useState<string>("");
  useEffect(() => {
    api.get("/models").then(setModels);
    api.get("/roles").then((rs) => setLeadDefault(rs.find((r: Json) => r.is_lead)?.model_tool_id ?? ""));
  }, []);
  return { models, leadDefault };
}

function SkillCard({ skill, models, leadDefault }: { skill: Json; models: Json[]; leadDefault: string }) {
  const navigate = useNavigate();
  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(skill.params.map((p: Json) => [p.name, p.default ?? ""])));
  const [model, setModel] = useState<number | null>(skill.lead_model_tool_id ?? null);
  const [error, setError] = useState("");
  const fallback = models.find((m) => m.id === Number(leadDefault))?.name ?? "Lead role default";
  async function run() {
    try {
      const r = await api.post(`/skills/${skill.id}/run`, { params, lead_model_tool_id: model });
      navigate(`/research/${r.session_id}`, { state: { autoSend: r.prompt } });
    } catch (e: any) { setError(e.message); }
  }
  return (
    <div className="card">
      <strong>{skill.name}</strong>
      <p className="muted">{skill.description}</p>
      {skill.params.map((p: Json) => (
        <Field key={p.name} label={p.label ?? p.name} value={params[p.name]} onChange={(v) => setParams({ ...params, [p.name]: v })} />
      ))}
      <label>Lead Analyst model</label>
      <ModelSelect models={models} value={model} onChange={setModel} defaultLabel={`${fallback} (Lead default)`} />
      {error && <p className="error">{error}</p>}
      <div className="actions"><button className="primary" onClick={run}>Run</button></div>
    </div>
  );
}

export default function Skills() {
  const crud = useCrud("/skills");
  const [editing, setEditing] = useState(false);
  const { models, leadDefault } = useModels();
  if (editing) {
    return (
      <>
        <div className="page" style={{ paddingBottom: 0 }}><button onClick={() => { setEditing(false); crud.reload(); }}>← Back to skills</button></div>
        <MasterDetail title="Edit skills" crud={crud}
          blank={{ slug: "", name: "", description: "", prompt_template: "", params: [{ name: "ticker", label: "Ticker" }], enabled: true }}>
          {(s, set) => (
            <>
              <Field label="Name" value={s.name} onChange={(name) => set({ name })} />
              <Field label="Slug" value={s.slug} onChange={(slug) => set({ slug })} />
              <Field label="Description" value={s.description} onChange={(description) => set({ description })} />
              <Field label="Prompt template (use {param} placeholders)" value={s.prompt_template}
                     onChange={(prompt_template) => set({ prompt_template })} multiline rows={6} />
              <JsonField label='Parameters: [{"name", "label", "default"}]' value={s.params} onChange={(params) => set({ params })} rows={6} />
              <label>Default Lead Analyst model</label>
              <ModelSelect models={models} value={s.lead_model_tool_id ?? null} defaultLabel="Lead role default"
                           onChange={(lead_model_tool_id) => set({ lead_model_tool_id })} />
              <Toggle label="Enabled" value={s.enabled} onChange={(enabled) => set({ enabled })} />
            </>
          )}
        </MasterDetail>
      </>
    );
  }
  return (
    <div className="page">
      <div className="inline" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Skills</h2>
        <button onClick={() => setEditing(true)}>Edit skills</button>
      </div>
      {crud.error && <p className="error">{crud.error}</p>}
      <div className="cards">
        {crud.items.filter((s) => s.enabled).map((s) => <SkillCard key={s.id} skill={s} models={models} leadDefault={leadDefault} />)}
      </div>
    </div>
  );
}
