/** Tools → Stock lists: named ticker lists (indexes such as the S&P 500, or your own) with a refresh schedule. */
import { useState } from "react";
import { api, Json } from "../api";
import { Field, MasterDetail, Toggle, useCrud } from "../admin";
import { errorText } from "../TwoPane";
import { useSettings } from "../settings";

const SCHEDULES: [string, string][] = [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["manual", "Manual only"]];

export default function StockLists({ header }: { header: React.ReactNode }) {
  const crud = useCrud("/stock-lists");
  const { fmt } = useSettings();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const [finding, setFinding] = useState(false);
  async function findSource(sl: Json) {
    setFinding(true); setMsg("");
    try {
      const saved = await crud.save(sl);  // save first, so the symbol and any edits are used
      if (!saved) { setMsg("Fill in the name and slug, then try again."); return; }
      const updated = await api.post(`/stock-lists/${saved.id}/find-source`);
      await crud.reload();
      crud.setSelected(updated);
      setMsg(`Found ${updated.tickers.length} tickers. ${updated.source_note}.`);
    } catch (e: any) { setMsg(errorText(e)); } finally { setFinding(false); }
  }

  async function refreshNow(sl: Json) {
    setBusy(true); setMsg("");
    try {
      const updated = await api.post(`/stock-lists/${sl.id}/refresh`);
      await crud.reload();
      crud.setSelected(updated);
      setMsg(`Refreshed: ${updated.tickers.length} tickers.`);
    } catch (e: any) { setMsg(errorText(e)); } finally { setBusy(false); }
  }

  return (
    <MasterDetail title="Stock lists" crud={crud} header={<>{header}
      <p className="muted small" style={{ maxWidth: 820 }}>Named ticker lists, such as an index, for rule-based scanners and for
        questions and skills (the Lead looks them up with the stock list tool). A list is refreshed only when something uses
        it and it's older than its schedule allows. Refreshing downloads the list (no AI model, no cost).</p></>}
      blank={{ slug: "", name: "", description: "", symbol: "", source: "web", url: "", column: "Ticker", fallback_url: "",
               fallback_column: "Symbol", min_count: 0, refresh: "weekly", tickers: [], enabled: true }}
      rowExtra={(sl) => <span className="muted small nowrap">{sl.tickers?.length ? `${sl.tickers.length} · ` : ""}{sl.refresh}</span>}>
      {(sl, set) => (
        <>
          <Field label="Name (e.g. &quot;S&P 500 (SPY)&quot;; questions can use the name, the part before the brackets, or the ETF symbol)"
                 value={sl.name} onChange={(name) => set({ name })} />
          <Field label="Slug (unique id)" value={sl.slug} onChange={(slug) => set({ slug })} />
          <Field label="Description" value={sl.description} onChange={(description) => set({ description })} />
          <label>Source</label>
          <select value={sl.source === "wikipedia" ? "web" : sl.source}
                  onChange={(e) => set({ source: e.target.value, refresh: e.target.value === "manual" ? "manual" : (sl.refresh === "manual" ? "weekly" : sl.refresh) })}>
            <option value="web">Downloaded: ETF holdings file or web page (CSV, Excel or HTML table)</option>
            <option value="manual">Manual: tickers you maintain</option>
          </select>
          {sl.source !== "manual" && (
            <>
              <div className="find-source">
                <div style={{ flex: 1 }}>
                  <Field label="ETF symbol, e.g. SPY (S&P 500), QQQ (Nasdaq-100), DIA (Dow 30), XLK" value={sl.symbol ?? ""}
                         onChange={(symbol) => set({ symbol })} />
                </div>
                <button className="act act-accent" disabled={finding || !(sl.symbol || sl.name)} onClick={() => findSource(sl)}
                        title="Find where to download the members, check it works, and save it">
                  {finding ? "Finding…" : "Find source"}
                </button>
                {sl.id && (
                  <button className="act" disabled={busy || finding} onClick={() => refreshNow(sl)}
                          title="Download the list now from its main source (or the backup if that fails)">
                    {busy ? "Refreshing…" : "Refresh now"}
                  </button>
                )}
              </div>
              {sl.id && (
                <p className="small list-status">
                  <strong>{sl.tickers?.length ?? 0} tickers</strong>
                  {sl.refreshed_at ? <span className="muted"> · last refreshed {fmt(sl.refreshed_at)}</span> : <span className="muted"> · not downloaded yet</span>}
                  {sl.last_source && <span className="muted"> · from the {sl.last_source === "main" ? "main source" : "backup"}</span>}
                  {sl.last_error && <><br /><span className="error">{sl.last_source === "backup" ? "" : "Last refresh failed: "}{sl.last_error}</span></>}
                  {msg && !finding && <><br /><span className="muted">{msg}</span></>}
                </p>
              )}
              {finding && <p className="muted small"><span className="spinner small" /> Searching for the ETF's holdings file and a
                backup, and checking each one. This can take a minute or two.</p>}
              <p className="muted small">Find source looks for the ETF's official daily holdings file (main source) using the
                cheapest Claude model with web search (about a cent or two), and a Wikipedia members table as backup (free).
                Only sources that download and parse correctly are saved. Use it again if a source moves.</p>
              {sl.source_note && <p className="small" style={{ color: "var(--ok-fg)" }}>✓ {sl.source_note}</p>}
              <div className="src-block">
                <div className="src-title">Main source <span className="muted small">· ETF holdings file or page (CSV, Excel or HTML table)</span>
                  {sl.last_source === "main" && <span className="badge badge-ok">current tickers</span>}</div>
                <Field label="Address" value={sl.url} onChange={(url) => set({ url })} />
                <Field label="Ticker column name" value={sl.column} onChange={(column) => set({ column })} />
              </div>
              <div className="src-block">
                <div className="src-title">Backup source <span className="muted small">· used if the main source fails (e.g. a Wikipedia table)</span>
                  {sl.last_source === "backup" && <span className="badge badge-warn">current tickers</span>}</div>
                <Field label="Address" value={sl.fallback_url ?? ""} onChange={(fallback_url) => set({ fallback_url })} />
                <Field label="Ticker column name" value={sl.fallback_column ?? "Symbol"} onChange={(fallback_column) => set({ fallback_column })} />
              </div>
              <label>Expected at least (tickers)</label>
              <input type="number" min={0} value={sl.min_count ?? 0} onChange={(e) => set({ min_count: Number(e.target.value) || 0 })} />
              <p className="muted small">A source that returns fewer is treated as incomplete (e.g. a page that shows only the top
                holdings) and the backup is used instead. 0 = no check.</p>
              <label>Refresh schedule</label>
              <select value={sl.refresh} onChange={(e) => set({ refresh: e.target.value })}>
                {SCHEDULES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <p className="muted small">Checked when the list is used: if it's older than this, it's downloaded first.
                Index members rarely change; weekly or monthly is usually enough.</p>
            </>
          )}
          <label>Tickers ({sl.tickers?.length ?? 0}){sl.source !== "manual" ? " · filled in by refresh" : ""}</label>
          <textarea rows={6} value={(sl.tickers ?? []).join(", ")} readOnly={sl.source !== "manual"}
                    onChange={(e) => set({ tickers: e.target.value.split(/[\s,]+/).map((x) => x.trim().toUpperCase()).filter(Boolean) })} />
          <Toggle label="Enabled" value={sl.enabled} onChange={(enabled) => set({ enabled })} />
        </>
      )}
    </MasterDetail>
  );
}
