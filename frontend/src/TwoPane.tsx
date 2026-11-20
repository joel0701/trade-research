/** Two-pane layout like Research (without the trace): list + profile on the left, details on the right. */
import { ReactNode, useEffect, useState } from "react";
import { Json } from "./api";
import { modelLabel, VENDOR_COLORS } from "./models";
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
export type Tone = "blue" | "amber" | "purple" | "green" | "teal";

/** A colour-coded group of fields with a header (used by the Skills and Scanners editors). */
export function Section({ tone, icon, title, hint, children }: {
  tone: Tone; icon?: ReactNode; title: string; hint?: ReactNode; children: ReactNode;
}) {
  return (
    <section className={`cfg tone-${tone}`}>
      <div className="cfg-head">
        {icon && <span className="cfg-icon">{icon}</span>}
        <h3>{title}</h3>
        {hint && <span className="cfg-hint">{hint}</span>}
      </div>
      <div className="cfg-body">{children}</div>
    </section>
  );
}

export function Dialog({ title, onClose, children, tone, icon, subtitle }: {
  title: string; onClose: () => void; children: ReactNode; tone?: Tone; icon?: ReactNode; subtitle?: ReactNode;
}) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${tone ? `tone-${tone} modal-toned` : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <div className="modal-title">
            {icon && <span className="cfg-icon">{icon}</span>}
            <div><strong>{title}</strong>{subtitle && <div className="modal-sub">{subtitle}</div>}</div>
          </div>
          <button className="link" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Lead model picker for run dialogs: only models whose vendor key is set. */
export function LeadModelSelect({ models, value, onChange, defaultLabel, label = "Lead Analyst model", hint }: {
  models: Json[]; value: number | null; onChange: (v: number | null) => void; defaultLabel: string;
  label?: string; hint?: string;
}) {
  return (
    <>
      <label>{label}{hint && <span className="muted small"> · {hint}</span>}</label>
      <select className="model-select" style={{ borderLeftColor: VENDOR_COLORS[models.find((m) => m.id === value && m.available)?.provider] ?? "var(--accent)" }}
              value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{defaultLabel}</option>
        {models.filter((m) => m.available).map((m) => <option key={m.id} value={m.id}>{modelLabel(m)}</option>)}
      </select>
    </>
  );
}

/** The server's error detail (e.g. "IB Gateway isn't reachable…") rather than raw JSON. */
export function errorText(e: any): string {
  const m = String(e?.message ?? e).match(/"detail":"((?:[^"\\]|\\.)*)"/);
  return m ? m[1].replace(/\\"/g, '"') : String(e?.message ?? e);
}
