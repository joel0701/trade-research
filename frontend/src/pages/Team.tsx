import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Field, MasterDetail, Toggle, useCrud } from "../admin";
import SourcePicker from "../SourcePicker";

/** The team: the Lead Analyst and the specialists it consults (stored as "roles" in the database). */
export default function Team() {
  const crud = useCrud("/team");
  const [models, setModels] = useState<Json[]>([]);
  useEffect(() => { api.get("/models").then(setModels); }, []);

  return (
    <MasterDetail title="Team" crud={crud}
      groups={[{ key: "lead", label: "Lead Analyst", match: (r) => !!r.is_lead },
               { key: "specialists", label: "Specialists", match: (r) => !r.is_lead }]}
      blank={{ slug: "", name: "", description: "", is_lead: false, model_tool_id: models[0]?.id ?? null,
               system_prompt: "", tool_ids: [], max_iterations: 12, enabled: true }}>
      {(r, set) => (
        <>
          <Field label="Name" value={r.name} onChange={(name) => set({ name })} />
          <Field label="Slug (the Lead calls it as consult_<slug>)" value={r.slug} onChange={(slug) => set({ slug })} />
          <Field label="Description (tells the Lead when to consult this specialist)" value={r.description}
                 onChange={(description) => set({ description })} multiline rows={2} />
          <label>Model</label>
          <select value={r.model_tool_id ?? ""} onChange={(e) => set({ model_tool_id: Number(e.target.value) })}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.provider_name}: {m.name} ({m.config.model_id}){m.available ? "" : ` — no API key (${m.key_env})`}
              </option>
            ))}
          </select>
          {models.find((m) => m.id === r.model_tool_id && !m.available) && (
            <p className="error small">This model's vendor has no API key in .env, so this member will fail until one is added.</p>
          )}
          <Field label="System prompt" value={r.system_prompt} onChange={(system_prompt) => set({ system_prompt })} multiline rows={8} />
          <label>Data sources it can use</label>
          <SourcePicker value={r.tool_ids} onChange={(tool_ids) => set({ tool_ids })}
                        hint="sources that aren't ready are skipped" />
          <label>Max tool-use iterations per run</label>
          <input type="number" value={r.max_iterations} onChange={(e) => set({ max_iterations: Number(e.target.value) })} />
          <Toggle label="Lead Analyst (only one member should be the Lead)" value={r.is_lead} onChange={(is_lead) => set({ is_lead })} />
          <Toggle label="Enabled" value={r.enabled} onChange={(enabled) => set({ enabled })} />
        </>
      )}
    </MasterDetail>
  );
}
