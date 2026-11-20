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
      blank={{ slug: "", name: "", description: "", source: "manual", url: "", column: "Symbol", refresh: "manual", tickers: [], enabled: true }}
      rowExtra={(sl) => <span className="muted small nowrap">{sl.tickers?.length ? `${sl.tickers.length} · ` : ""}{sl.refresh}</span>}>
      {(sl, set) => (
        <>
          <Field label="Name (how questions and skills refer to it, e.g. &quot;S&P 500&quot;)" value={sl.name} onChange={(name) => set({ name })} />
          <Field label="Slug (unique id)" value={sl.slug} onChange={(slug) => set({ slug })} />
          <Field label="Description" value={sl.description} onChange={(description) => set({ description })} />
          <label>Source</label>
          <select value={sl.source} onChange={(e) => set({ source: e.target.value, refresh: e.target.value === "manual" ? "manual" : (sl.refresh === "manual" ? "weekly" : sl.refresh) })}>
            <option value="wikipedia">Wikipedia table (downloaded on refresh)</option>
            <option value="manual">Manual: tickers you maintain</option>
          </select>
          {sl.source === "wikipedia" && (
            <>
              <Field label="Wikipedia page address" value={sl.url} onChange={(url) => set({ url })} />
              <Field label="Ticker column name in the page's table" value={sl.column} onChange={(column) => set({ column })} />
              <label>Refresh schedule</label>
              <select value={sl.refresh} onChange={(e) => set({ refresh: e.target.value })}>
                {SCHEDULES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <p className="muted small">Checked when the list is used: if it's older than this, it's downloaded first.
                Index members rarely change; weekly or monthly is usually enough.</p>
            </>
          )}
          <label>Tickers ({sl.tickers?.length ?? 0}){sl.source === "wikipedia" ? " · filled in by refresh" : ""}</label>
          <textarea rows={6} value={(sl.tickers ?? []).join(", ")} readOnly={sl.source !== "manual"}
                    onChange={(e) => set({ tickers: e.target.value.split(/[\s,]+/).map((x) => x.trim().toUpperCase()).filter(Boolean) })} />
          {sl.id && sl.source === "wikipedia" && (
            <p className="small">
              {sl.refreshed_at ? <span className="muted">Last refreshed {fmt(sl.refreshed_at)}. </span> : <span className="muted">Not downloaded yet. </span>}
              {sl.last_error && <span className="error">Last refresh failed: {sl.last_error} </span>}
              <button className="link" disabled={busy} onClick={() => refreshNow(sl)}>{busy ? "Refreshing…" : "Refresh now"}</button>
              {msg && <span className="muted"> {msg}</span>}
            </p>
          )}
          <Toggle label="Enabled" value={sl.enabled} onChange={(enabled) => set({ enabled })} />
        </>
      )}
    </MasterDetail>
  );
}
