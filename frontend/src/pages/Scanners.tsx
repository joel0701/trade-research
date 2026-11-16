import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Field, JsonField, MasterDetail, Toggle, useCrud } from "../admin";
import SourcePicker from "../SourcePicker";

type Option = { code: string; label: string };

const RULES_HELP = 'Rules: {"all": [...]}, {"any": [...]}, {"not": rule}, or a condition like '
  + '{"field": "rsi_14", "op": "<", "value": 30} / {"field": "close", "op": ">", "field2": "sma_200"}. '
  + "Fields: close, volume, avg_volume_20, rel_volume, sma_20, sma_50, sma_200, rsi_14, macd, macd_signal, "
  + "macd_hist, atr_14, pct_change_1d/5d/20d, high_52w, low_52w, pct_from_52w_high.";

function NumberField({ label, value, onChange, hint }:
  { label: string; value: number | undefined; onChange: (v: number | undefined) => void; hint?: string }) {
  return (
    <>
      <label>{label}{hint && <span className="muted small"> · {hint}</span>}</label>
      <input type="number" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />
    </>
  );
}

export default function Scanners() {
  const crud = useCrud("/screeners");
  const navigate = useNavigate();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [codes, setCodes] = useState<Option[]>([]);
  const [locations, setLocations] = useState<Option[]>([]);
  useEffect(() => {
    api.get("/scanner-options").then((o) => { setCodes(o.scan_codes); setLocations(o.locations); }).catch(() => {});
  }, []);

  async function run(id: number) {
    setRunning(true); setError("");
    try {
      const r = await api.post(`/screeners/${id}/run`);
      navigate(`/research/${r.session_id}`, { state: { autoSend: r.prompt } });
    } catch (e: any) {
      // Show the server's explanation (e.g. IB Gateway isn't running) rather than raw JSON.
      const m = String(e.message).match(/"detail":"(.*)"/);
      setError(m ? m[1] : e.message);
    } finally { setRunning(false); }
  }

  return (
    <MasterDetail title="Scanners" crud={crud}
      rowExtra={(s) => <span className={`badge ${s.source === "ibkr" ? "badge-ibkr" : ""}`}>{s.source === "ibkr" ? "IBKR" : "Rules"}</span>}
      blank={{ slug: "", name: "", description: "", source: "rules", params: {}, universe: [], rules: { all: [] }, tool_ids: [],
               sort_by: null, limit: 25, follow_up_prompt: "Review these results and pick the most promising candidates.", enabled: true }}>
      {(s, set) => {
        const p = s.params ?? {};
        const setP = (patch: Record<string, unknown>) => set({ params: { ...p, ...patch } });
        return (
          <>
            {s.id && (
              <div className="actions" style={{ marginTop: 0, marginBottom: 8 }}>
                <button className="primary" disabled={running} onClick={() => run(s.id)}>
                  {running ? "Scanning…" : "Run scanner → new session"}
                </button>
                {error && <span className="error">{error}</span>}
              </div>
            )}
            <Field label="Name" value={s.name} onChange={(name) => set({ name })} />
            <Field label="Slug" value={s.slug} onChange={(slug) => set({ slug })} />
            <Field label="Description" value={s.description} onChange={(description) => set({ description })} />
            <label>Type</label>
            <select value={s.source ?? "rules"} onChange={(e) => set({ source: e.target.value,
              params: e.target.value === "ibkr" && !p.scan_code ? { scan_code: "TOP_PERC_GAIN", location: "STK.US.MAJOR", rows: 25 } : p })}>
              <option value="rules">Rule-based: your rules over a list of tickers (Yahoo data)</option>
              <option value="ibkr">IBKR market scanner: IBKR scans the whole market (needs IB Gateway)</option>
            </select>

            {s.source === "ibkr" ? (
              <>
                <label>IBKR scan</label>
                <select value={p.scan_code ?? ""} onChange={(e) => setP({ scan_code: e.target.value })}>
                  {codes.map((c) => <option key={c.code} value={c.code}>{c.label} ({c.code})</option>)}
                  {p.scan_code && !codes.some((c) => c.code === p.scan_code) && <option value={p.scan_code}>{p.scan_code}</option>}
                </select>
                <p className="muted small">IBKR defines how each scan ranks stocks; it publishes the scan types and filters but not every formula.</p>
                <label>Market</label>
                <select value={p.location ?? "STK.US.MAJOR"} onChange={(e) => setP({ location: e.target.value })}>
                  {locations.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
                </select>
                <div className="grid-3">
                  <div><NumberField label="Results from IBKR" hint="max 50" value={p.rows} onChange={(rows) => setP({ rows })} /></div>
                  <div><NumberField label="Min price ($)" value={p.above_price} onChange={(above_price) => setP({ above_price })} /></div>
                  <div><NumberField label="Min volume" value={p.above_volume} onChange={(above_volume) => setP({ above_volume })} /></div>
                  <div><NumberField label="Min market cap" hint="$ millions" value={p.market_cap_above} onChange={(market_cap_above) => setP({ market_cap_above })} /></div>
                </div>
                <JsonField label="Extra rules (optional) applied to IBKR's results" value={s.rules} onChange={(rules) => set({ rules })} rows={5} />
                <p className="muted small">{RULES_HELP} Leave as {"{}"} to keep IBKR's results as they are.</p>
              </>
            ) : (
              <>
                <Field label="Universe (comma-separated tickers)" value={(s.universe ?? []).join(", ")} multiline rows={3}
                       onChange={(v) => set({ universe: v.split(/[\s,]+/).map((x) => x.trim().toUpperCase()).filter(Boolean) })} />
                <JsonField label="Rules" value={s.rules} onChange={(rules) => set({ rules })} rows={10} />
                <p className="muted small">{RULES_HELP}</p>
              </>
            )}
            <Field label={s.source === "ibkr" ? "Sort by (empty = IBKR's ranking; prefix - for descending)" : "Sort by (field; prefix with - for descending)"}
                   value={s.sort_by ?? ""} onChange={(v) => set({ sort_by: v || null })} />
            <label>Max results</label>
            <input type="number" value={s.limit} onChange={(e) => set({ limit: Number(e.target.value) })} />
            <label>Data sources for the follow-up analysis</label>
            <p className="muted small">
              The session this scanner opens uses only these sources, and only these are checked before it runs.
              {(s.tool_ids ?? []).length === 0 && <strong> None chosen: the full team's sources are used.</strong>}
            </p>
            <SourcePicker value={s.tool_ids ?? []} onChange={(tool_ids) => set({ tool_ids })} />
            <Field label="Follow-up prompt for the Lead" value={s.follow_up_prompt}
                   onChange={(follow_up_prompt) => set({ follow_up_prompt })} multiline rows={3} />
            <Toggle label="Enabled" value={s.enabled} onChange={(enabled) => set({ enabled })} />
          </>
        );
      }}
    </MasterDetail>
  );
}
