import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Json } from "../api";
import { Field, JsonField, Toggle, useCrud } from "../admin";
import { Dialog, errorText, LeadModelSelect, Section, TwoPane, useDraft } from "../TwoPane";
import { Chip, Gear, Prompt, Radar } from "../icons";
import { useSources } from "../SourcePicker";
import { PricingIcon } from "../Pricing";

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

const BLANK = { slug: "", name: "New scanner", description: "", source: "rules", params: {}, universe: [], rules: { all: [] },
                sort_by: null, limit: 25, lead_model_tool_id: null, enabled: true,
                follow_up_prompt: "Review these results and pick the most promising candidates." };

/** What the scan itself uses. After the results, the Lead analyses them like any research question. */
function ScanData({ source }: { source: string }) {
  const { tools, status } = useSources();
  const bySlug = (slug: string) => tools.find((t) => t.slug === slug);
  const item = (slug: string, role: string) => {
    const t = bySlug(slug);
    if (!t) return null;
    const st = status[t.id];
    return (
      <li key={slug} title={t.notes ?? ""}>
        <span className={`dot ${st?.available ? "ok" : "off"}`} /><PricingIcon pricing={st?.pricing} />
        <strong>{t.name}</strong> <span className="muted small">· {role}{st && !st.available ? ` · ${st.reason}` : ""}</span>
      </li>
    );
  };
  return (
    <>
      <label>Scan data</label>
      <ul className="plain scan-data">
        {source === "ibkr"
          ? [item("ibkr_gw_scanner", "runs the scan"), item("technical_snapshot", "adds indicator columns (RSI, moving averages…) used by rules and sorting")]
          : [item("technical_snapshot", "daily bars for each ticker; indicators computed here")]}
      </ul>
      <p className="muted small">
        This page configures only the scan. After the results come in, the Lead analyses them, and answers any
        follow-up questions, like any research question: with the full team and all available data sources.
      </p>
    </>
  );
}

/** Asks for this run's settings (pre-filled from the scanner; not saved), then runs it into a new session. */
function RunScannerDialog({ scanner, models, leadDefault, codes, locations, onClose }: {
  scanner: Json; models: Json[]; leadDefault: string; codes: Option[]; locations: Option[]; onClose: () => void;
}) {
  const navigate = useNavigate();
  const ibkr = scanner.source === "ibkr";
  const [p, setP] = useState<Record<string, any>>({ ...(scanner.params ?? {}) });
  const [universe, setUniverse] = useState((scanner.universe ?? []).join(", "));
  const [model, setModel] = useState<number | null>(scanner.lead_model_tool_id ?? null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const tickers = universe.split(/[\s,]+/).map((x: string) => x.trim().toUpperCase()).filter(Boolean);
  const code = codes.find((c) => c.code === p.scan_code)?.label ?? p.scan_code;
  const market = locations.find((l) => l.code === (p.location ?? "STK.US.MAJOR"))?.label ?? p.location;

  async function run() {
    setRunning(true); setError("");
    try {
      const body = ibkr
        ? { params: { rows: p.rows, above_price: p.above_price, above_volume: p.above_volume, market_cap_above: p.market_cap_above },
            lead_model_tool_id: model }
        : { universe: tickers, lead_model_tool_id: model };
      const r = await api.post(`/screeners/${scanner.id}/run`, body);
      navigate(`/research/${r.session_id}`, { state: { autoSend: r.prompt, hidden: !!r.hidden } });
    } catch (e: any) { setError(errorText(e)); setRunning(false); }
  }

  return (
    <Dialog title={`Run: ${scanner.name}`} onClose={onClose} tone="amber" icon={<Radar />}
            subtitle={ibkr ? <>IBKR scan <strong>{code}</strong> over <strong>{market}</strong></> : "Rule-based scan of your ticker list"}>
      <p className="muted small">Changes here apply to this run only.</p>
      <form onSubmit={(e) => { e.preventDefault(); run(); }}>
        {ibkr ? (
          <div className="grid-3">
            <div><NumberField label="Results from IBKR" hint="max 50" value={p.rows} onChange={(rows) => setP({ ...p, rows })} /></div>
            <div><NumberField label="Min price ($)" value={p.above_price} onChange={(above_price) => setP({ ...p, above_price })} /></div>
            <div><NumberField label="Min volume" value={p.above_volume} onChange={(above_volume) => setP({ ...p, above_volume })} /></div>
            <div><NumberField label="Min market cap" hint="$ millions" value={p.market_cap_above} onChange={(market_cap_above) => setP({ ...p, market_cap_above })} /></div>
          </div>
        ) : (
          <>
            <label>Tickers to scan ({tickers.length})</label>
            <textarea rows={4} value={universe} onChange={(e) => setUniverse(e.target.value)} />
          </>
        )}
        <LeadModelSelect models={models} value={model} onChange={setModel} label="Model for the analysis"
                         hint="follow-up questions use the Research default" defaultLabel={`Research default (${leadDefault || "Lead role model"})`} />
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button className="primary" disabled={running || (!ibkr && tickers.length === 0)}>{running ? "Scanning…" : "Run"}</button>
          <button type="button" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function Scanners() {
  const crud = useCrud("/screeners");
  const { draft, set } = useDraft(crud.selected);
  const [codes, setCodes] = useState<Option[]>([]);
  const [locations, setLocations] = useState<Option[]>([]);
  const [models, setModels] = useState<Json[]>([]);
  const [leadDefault, setLeadDefault] = useState("");
  const [running, setRunning] = useState(false);
  useEffect(() => {
    api.get("/scanner-options").then((o) => { setCodes(o.scan_codes); setLocations(o.locations); }).catch(() => {});
    Promise.all([api.get("/models"), api.get("/roles")]).then(([ms, rs]) => {
      setModels(ms);
      const id = rs.find((r: Json) => r.is_lead)?.model_tool_id;
      setLeadDefault(ms.find((m: Json) => m.id === id)?.name ?? "");
    }).catch(() => {});
  }, []);
  useEffect(() => { if (!crud.selected && crud.items.length) crud.setSelected(crud.items[0]); }, [crud.items]);

  const s = draft;
  const p = s?.params ?? {};
  const setP = (patch: Record<string, unknown>) => set({ params: { ...p, ...patch } });
  const badge = (x: Json) => <span className={`badge ${x.source === "ibkr" ? "badge-ibkr" : ""}`}>{x.source === "ibkr" ? "IBKR" : "Rules"}</span>;

  return (
    <TwoPane title="Scanners" panelKey="scanners" items={crud.items} selectedId={crud.selected?.id}
             onSelect={crud.setSelected} onNew={() => crud.setSelected(structuredClone(BLANK))} rowExtra={badge}>
      {!s ? <p className="muted">Select a scanner, or create one with + New.</p> : (
        <div className="detail">
          <div className="detail-head">
            <div>
              <h2>{s.name || "Untitled scanner"} {badge(s)}</h2>
              {s.description && <p className="muted">{s.description}</p>}
            </div>
            <button className="primary run-btn" disabled={!s.id || !s.enabled}
                    title={!s.id ? "Save the scanner first" : !s.enabled ? "Enable the scanner to run it" : "Run this scanner"}
                    onClick={() => setRunning(true)}>▶ Run</button>
          </div>
          <div className="cfg-stack">
            <Section tone="blue" icon={<Gear />} title="Setup">
            <Field label="Name" value={s.name} onChange={(name) => set({ name })} />
            <div className="grid-2">
              <div><Field label="Slug" value={s.slug} onChange={(slug) => set({ slug })} /></div>
              <div><Field label="Description" value={s.description} onChange={(description) => set({ description })} /></div>
            </div>
            <Toggle label="Enabled" value={s.enabled} onChange={(enabled) => set({ enabled })} />
            </Section>
            <Section tone="amber" icon={<Radar />} title="Scan" hint="no LLM: IBKR or the rule engine finds the stocks">
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
            <ScanData source={s.source} />
            </Section>
            <Section tone="purple" icon={<Prompt />} title="Analysis" hint="the Lead's task once results are in (full team)">
            <Field label="Task for the Lead" value={s.follow_up_prompt}
                   onChange={(follow_up_prompt) => set({ follow_up_prompt })} multiline rows={3} />
            </Section>
            <Section tone="green" icon={<Chip />} title="Model" hint="follow-up questions use the Research default">
            <LeadModelSelect models={models} value={s.lead_model_tool_id ?? null}
                             onChange={(lead_model_tool_id) => set({ lead_model_tool_id })}
                             label="Model for the analysis"
                             defaultLabel={`Research default (${leadDefault || "Lead role model"})`} />
            </Section>
            <div className="cfg-actions">
              {crud.error && <p className="error">{crud.error}</p>}
              <button className="primary" onClick={() => crud.save(s)}>Save</button>
              {s.id && <button onClick={() => crud.remove(s)}>Delete</button>}
            </div>
          </div>
        </div>
      )}
      {running && s?.id && (
        <RunScannerDialog scanner={crud.items.find((x) => x.id === s.id) ?? s} models={models} leadDefault={leadDefault}
                          codes={codes} locations={locations} onClose={() => setRunning(false)} />
      )}
    </TwoPane>
  );
}
