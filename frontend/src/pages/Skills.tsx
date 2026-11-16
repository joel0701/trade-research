import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Json } from "../api";
import { Field, JsonField, MasterDetail, Toggle, useCrud } from "../admin";

function SkillCard({ skill }: { skill: Json }) {
  const navigate = useNavigate();
  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(skill.params.map((p: Json) => [p.name, p.default ?? ""])));
  const [error, setError] = useState("");
  async function run() {
    try {
      const r = await api.post(`/skills/${skill.id}/run`, { params });
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
      {error && <p className="error">{error}</p>}
      <div className="actions"><button className="primary" onClick={run}>Run</button></div>
    </div>
  );
}

export default function Skills() {
  const crud = useCrud("/skills");
  const [editing, setEditing] = useState(false);
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
      <div className="cards">
        {crud.items.filter((s) => s.enabled).map((s) => <SkillCard key={s.id} skill={s} />)}
      </div>
    </div>
  );
}
