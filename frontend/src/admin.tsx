/** Shared bits for the configuration pages. */
import { useEffect, useState } from "react";
import { api, Json } from "./api";

export function useCrud(path: string) {
  const [items, setItems] = useState<Json[]>([]);
  const [selected, setSelected] = useState<Json | null>(null);
  const [error, setError] = useState("");
  const reload = () => api.get<Json[]>(path).then(setItems);
  useEffect(() => { reload(); }, [path]);

  async function save(item: Json) {
    setError("");
    try {
      const saved = item.id ? await api.put(`${path}/${item.id}`, item) : await api.post(path, item);
      await reload();
      setSelected(saved);
    } catch (e: any) { setError(e.message); }
  }
  async function remove(item: Json) {
    if (!item.id || !confirm(`Delete ${item.name}?`)) return;
    await api.del(`${path}/${item.id}`);
    setSelected(null);
    reload();
  }
  return { items, selected, setSelected, save, remove, error, reload };
}

/** A textarea that edits a JSON value and only commits valid JSON. */
export function JsonField({ label, value, onChange, rows = 8 }:
  { label: string; value: unknown; onChange: (v: any) => void; rows?: number }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [bad, setBad] = useState(false);
  useEffect(() => { setText(JSON.stringify(value, null, 2)); setBad(false); }, [JSON.stringify(value)]);
  return (
    <>
      <label>{label} {bad && <span className="error">(invalid JSON)</span>}</label>
      <textarea rows={rows} value={text} style={{ fontFamily: "ui-monospace, monospace", fontSize: 13 }}
        onChange={(e) => {
          setText(e.target.value);
          try { onChange(JSON.parse(e.target.value)); setBad(false); } catch { setBad(true); }
        }} />
    </>
  );
}

export function Field({ label, value, onChange, multiline = false, rows = 4 }:
  { label: string; value: string; onChange: (v: string) => void; multiline?: boolean; rows?: number }) {
  return (
    <>
      <label>{label}</label>
      {multiline
        ? <textarea rows={rows} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
        : <input value={value ?? ""} onChange={(e) => onChange(e.target.value)} />}
    </>
  );
}

export function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="inline"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />{label}</label>
  );
}

/** Left list + right editor layout. */
export function MasterDetail({ title, crud, blank, children }: {
  title: string; crud: ReturnType<typeof useCrud>; blank: Json;
  children: (item: Json, set: (patch: Json) => void) => React.ReactNode;
}) {
  const { items, selected, setSelected, save, remove, error } = crud;
  const [draft, setDraft] = useState<Json | null>(null);
  useEffect(() => { setDraft(selected ? structuredClone(selected) : null); }, [selected]);
  return (
    <div className="page">
      <div className="inline" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <button onClick={() => setSelected(structuredClone(blank))}>+ Add</button>
      </div>
      <div className="split">
        <div className="list">
          {items.map((it) => (
            <div key={it.id} className={`row ${selected?.id === it.id ? "active" : ""} ${it.enabled === false ? "off" : ""}`}
                 onClick={() => setSelected(it)}>
              {it.name}{it.kind && <span className="badge">{it.kind}</span>}{it.is_lead && <span className="badge">lead</span>}
            </div>
          ))}
        </div>
        <div className="card">
          {draft ? (
            <>
              {children(draft, (patch) => setDraft({ ...draft, ...patch }))}
              {error && <p className="error">{error}</p>}
              <div className="actions">
                <button className="primary" onClick={() => save(draft)}>Save</button>
                {draft.id && <button onClick={() => remove(draft)}>Delete</button>}
              </div>
            </>
          ) : <p className="muted">Select an item to edit, or add a new one.</p>}
        </div>
      </div>
    </div>
  );
}
