/** Checklist of data sources grouped by category, with ready / not-set-up dots. */
import { useEffect, useState } from "react";
import { api, Json } from "./api";

export function useSources() {
  const [tools, setTools] = useState<Json[]>([]);
  const [caps, setCaps] = useState<Json[]>([]);
  const [status, setStatus] = useState<Record<number, Json>>({});
  useEffect(() => {
    api.get("/tools").then((ts) => setTools(ts.filter((t: Json) => t.kind !== "model")));
    api.get("/capabilities").then(setCaps);
    api.get("/tools-status").then((ss) => setStatus(Object.fromEntries(ss.map((x: Json) => [x.id, x]))));
  }, []);
  return { tools, caps, status };
}

export default function SourcePicker({ value, onChange, hint }: {
  value: number[]; onChange: (ids: number[]) => void; hint?: string;
}) {
  const { tools, caps, status } = useSources();
  // Each source appears once, under its first category.
  const primary = (t: Json) => (t.capabilities?.[0] ?? "other");
  const toggle = (id: number, on: boolean) => onChange(on ? [...value, id] : value.filter((x) => x !== id));
  return (
    <>
      <p className="muted small">● ready · ○ not set up yet{hint ? ` · ${hint}` : ""}</p>
      {caps.filter((c) => tools.some((t) => primary(t) === c.slug)).map((c) => {
        const list = tools.filter((t) => primary(t) === c.slug);
        return (
          <div key={c.slug} className="role-group">
            <div className="list-group">
              {c.label} <span className="count">({list.filter((t) => value.includes(t.id)).length}/{list.length})</span>
            </div>
            <div className="checks">
              {list.map((t) => {
                const st = status[t.id];
                return (
                  <label key={t.id} className={st && !st.available ? "off" : ""} title={st?.reason ?? ""}>
                    <input type="checkbox" checked={value.includes(t.id)} onChange={(e) => toggle(t.id, e.target.checked)} />
                    <span className={`dot ${st?.available ? "ok" : "off"}`} />{t.name}
                  </label>
                );
              })}
            </div>
          </div>
        );
      })}
    </>
  );
}
