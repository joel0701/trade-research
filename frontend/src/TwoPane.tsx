/** Two-pane layout like Research (without the trace): list + profile on the left, details on the right. */
import { ReactNode, useEffect, useState } from "react";
import { Json } from "./api";
import ProfileCard from "./ProfileCard";
import { ResizeHandle, usePanelWidth } from "./Resizable";

export function TwoPane({ title, items, selectedId, onSelect, onNew, rowExtra, panelKey, children }: {
  title: string;
  items: Json[];
  selectedId: number | null | undefined;
  onSelect: (item: Json) => void;
  onNew: () => void;
  rowExtra?: (item: Json) => ReactNode;
  panelKey: string;
  children: ReactNode;
}) {
  const left = usePanelWidth(`panel.${panelKey}`, 280, 200, 480);
  return (
    <div className="research twopane">
      <aside className="sidebar" style={{ width: left.width }}>
        <div className="pane-head">
          <strong>{title}</strong>
          <button className="primary small-btn" onClick={onNew}>+ New</button>
        </div>
        <div className="session-scroll">
          {items.map((it) => (
            <div key={it.id} className={`item ${it.id === selectedId ? "active" : ""} ${it.enabled === false ? "off" : ""}`}
                 onClick={() => onSelect(it)} title={it.description ?? ""}>
              <span>{it.name}</span>
              {rowExtra?.(it)}
            </div>
          ))}
          {items.length === 0 && <p className="muted small" style={{ padding: 8 }}>None yet. Click + New.</p>}
        </div>
        <ProfileCard />
      </aside>
      <ResizeHandle side="left" panel={left} label={`Resize ${title.toLowerCase()} list`} />
      <section className="detail-pane">{children}</section>
    </div>
  );
}

/** Editable copy of the selected item (saved explicitly). */
export function useDraft(selected: Json | null) {
  const [draft, setDraft] = useState<Json | null>(null);
  useEffect(() => { setDraft(selected ? structuredClone(selected) : null); }, [selected]);
  return { draft, set: (patch: Json) => setDraft((d) => (d ? { ...d, ...patch } : d)) };
}

/** A centered dialog with a title, body and actions. */
export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <strong>{title}</strong>
          <button className="link" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Lead model picker for run dialogs: only models whose vendor key is set. */
export function LeadModelSelect({ models, value, onChange, defaultLabel }: {
  models: Json[]; value: number | null; onChange: (v: number | null) => void; defaultLabel: string;
}) {
  return (
    <>
      <label>Lead Analyst model</label>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{defaultLabel}</option>
        {models.filter((m) => m.available).map((m) => <option key={m.id} value={m.id}>{m.provider_name}: {m.name}</option>)}
      </select>
    </>
  );
}

/** The server's error detail (e.g. "IB Gateway isn't reachable…") rather than raw JSON. */
export function errorText(e: any): string {
  const m = String(e?.message ?? e).match(/"detail":"((?:[^"\\]|\\.)*)"/);
  return m ? m[1].replace(/\\"/g, '"') : String(e?.message ?? e);
}
