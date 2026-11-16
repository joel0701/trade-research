import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Field, MasterDetail, Toggle, useCrud } from "../admin";

export default function Roles() {
  const crud = useCrud("/roles");
  const [tools, setTools] = useState<Json[]>([]);
  useEffect(() => { api.get("/tools").then(setTools); }, []);
  const models = tools.filter((t) => t.kind === "model");
  const usable = tools.filter((t) => t.kind !== "model");
  return (
    <MasterDetail title="Roles" crud={crud}
      blank={{ slug: "", name: "", description: "", is_lead: false, model_tool_id: models[0]?.id ?? null,
               system_prompt: "", tool_ids: [], max_iterations: 12, enabled: true }}>
      {(r, set) => (
        <>
          <Field label="Name" value={r.name} onChange={(name) => set({ name })} />
          <Field label="Slug (the Lead calls it as consult_<slug>)" value={r.slug} onChange={(slug) => set({ slug })} />
          <Field label="Description (tells the Lead when to consult this role)" value={r.description}
                 onChange={(description) => set({ description })} multiline rows={2} />
          <label>Model</label>
          <select value={r.model_tool_id ?? ""} onChange={(e) => set({ model_tool_id: Number(e.target.value) })}>
            {models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.config.model_id}, effort {m.config.effort})</option>)}
          </select>
          <Field label="System prompt" value={r.system_prompt} onChange={(system_prompt) => set({ system_prompt })} multiline rows={8} />
          <label>Tools this role can use</label>
          <div className="checks">
            {usable.map((t) => (
              <label key={t.id} className={t.enabled ? "" : "off"}>
                <input type="checkbox" checked={r.tool_ids.includes(t.id)}
                  onChange={(e) => set({ tool_ids: e.target.checked ? [...r.tool_ids, t.id] : r.tool_ids.filter((x: number) => x !== t.id) })} />
                {t.name}{!t.enabled && " (disabled)"}
              </label>
            ))}
          </div>
          <label>Max tool-use iterations per run</label>
          <input type="number" value={r.max_iterations} onChange={(e) => set({ max_iterations: Number(e.target.value) })} />
          <Toggle label="Lead role (only one role should be the lead)" value={r.is_lead} onChange={(is_lead) => set({ is_lead })} />
          <Toggle label="Enabled" value={r.enabled} onChange={(enabled) => set({ enabled })} />
        </>
      )}
    </MasterDetail>
  );
}
