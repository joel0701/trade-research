import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Field, JsonField, MasterDetail, Toggle, useCrud } from "../admin";

const RULES_HELP = 'Rules: {"all": [...]}, {"any": [...]}, {"not": rule}, or a condition like '
  + '{"field": "rsi_14", "op": "<", "value": 30} / {"field": "close", "op": ">", "field2": "sma_200"}. '
  + "Fields: close, volume, avg_volume_20, rel_volume, sma_20, sma_50, sma_200, rsi_14, macd, macd_signal, "
  + "macd_hist, atr_14, pct_change_1d/5d/20d, high_52w, low_52w, pct_from_52w_high.";

export default function Screeners() {
  const crud = useCrud("/screeners");
  const navigate = useNavigate();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  async function run(id: number) {
    setRunning(true); setError("");
    try {
      const r = await api.post(`/screeners/${id}/run`);
      navigate(`/research/${r.session_id}`, { state: { autoSend: r.prompt } });
    } catch (e: any) { setError(e.message); } finally { setRunning(false); }
  }

  return (
    <MasterDetail title="Screeners" crud={crud}
      blank={{ slug: "", name: "", description: "", universe: [], rules: { all: [] }, sort_by: null, limit: 25,
               follow_up_prompt: "Review these screener results and pick the most promising candidates.", enabled: true }}>
      {(s, set) => (
        <>
          {s.id && (
            <div className="actions" style={{ marginTop: 0, marginBottom: 8 }}>
              <button className="primary" disabled={running} onClick={() => run(s.id)}>
                {running ? "Scanning…" : "Run screener → new session"}
              </button>
              {error && <span className="error">{error}</span>}
            </div>
          )}
          <Field label="Name" value={s.name} onChange={(name) => set({ name })} />
          <Field label="Slug" value={s.slug} onChange={(slug) => set({ slug })} />
          <Field label="Description" value={s.description} onChange={(description) => set({ description })} />
          <Field label="Universe (comma-separated tickers)" value={s.universe.join(", ")} multiline rows={3}
                 onChange={(v) => set({ universe: v.split(/[\s,]+/).map((x) => x.trim().toUpperCase()).filter(Boolean) })} />
          <JsonField label="Rules" value={s.rules} onChange={(rules) => set({ rules })} rows={10} />
          <p className="muted">{RULES_HELP}</p>
          <Field label="Sort by (field; prefix with - for descending)" value={s.sort_by ?? ""} onChange={(v) => set({ sort_by: v || null })} />
          <label>Max results</label>
          <input type="number" value={s.limit} onChange={(e) => set({ limit: Number(e.target.value) })} />
          <Field label="Follow-up prompt for the Lead" value={s.follow_up_prompt}
                 onChange={(follow_up_prompt) => set({ follow_up_prompt })} multiline rows={3} />
          <Toggle label="Enabled" value={s.enabled} onChange={(enabled) => set({ enabled })} />
        </>
      )}
    </MasterDetail>
  );
}
