import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Field, MasterDetail, Toggle, useCrud } from "../admin";

export default function Roles() {
  const crud = useCrud("/roles");
  const [tools, setTools] = useState<Json[]>([]);
  const [models, setModels] = useState<Json[]>([]);
  const [caps, setCaps] = useState<Json[]>([]);
  const [status, setStatus] = useState<Record<number, Json>>({});
  useEffect(() => {
    api.get("/tools").then(setTools);
    api.get("/models").then(setModels);
    api.get("/capabilities").then(setCaps);
    api.get("/tools-status").then((ss) => setStatus(Object.fromEntries(ss.map((x: Json) => [x.id, x]))));
  }, []);
  const usable = tools.filter((t) => t.kind !== "model");
  // A source appears under its first category so each checkbox shows once.
  const primary = (t: Json) => (t.capabilities?.[0] ?? "other");
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
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.provider_name}: {m.name} ({m.config.model_id}){m.available ? "" : ` — no API key (${m.key_env})`}
              </option>
            ))}
          </select>
          {models.find((m) => m.id === r.model_tool_id && !m.available) && (
            <p className="error small">This model's vendor has no API key in .env, so this role will fail until one is added.</p>
          )}
          <Field label="System prompt" value={r.system_prompt} onChange={(system_prompt) => set({ system_prompt })} multiline rows={8} />
          <label>Data sources this role can use</label>
          <p className="muted small">● ready · ○ not set up yet (the role skips it until it is; see Tools)</p>
          {caps.filter((c) => usable.some((t) => primary(t) === c.slug)).map((c) => (
            <div key={c.slug} className="role-group">
              <div className="list-group">
                {c.label} <span className="count">({usable.filter((t) => primary(t) === c.slug).length})</span>
              </div>
              <div className="checks">
                {usable.filter((t) => primary(t) === c.slug).map((t) => {
                  const st = status[t.id];
                  return (
                    <label key={t.id} className={st && !st.available ? "off" : ""} title={st?.reason ?? ""}>
                      <input type="checkbox" checked={r.tool_ids.includes(t.id)}
                        onChange={(e) => set({ tool_ids: e.target.checked ? [...r.tool_ids, t.id] : r.tool_ids.filter((x: number) => x !== t.id) })} />
                      <span className={`dot ${st?.available ? "ok" : "off"}`} />{t.name}
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
          <label>Max tool-use iterations per run</label>
          <input type="number" value={r.max_iterations} onChange={(e) => set({ max_iterations: Number(e.target.value) })} />
          <Toggle label="Lead role (only one role should be the lead)" value={r.is_lead} onChange={(is_lead) => set({ is_lead })} />
          <Toggle label="Enabled" value={r.enabled} onChange={(enabled) => set({ enabled })} />
        </>
      )}
    </MasterDetail>
  );
}
