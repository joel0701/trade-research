/** Trace log: every Lead and specialist run in a session, with tool calls, timing, tokens and cost. */
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, Json } from "./api";

type Run = {
  id: number; role_slug: string; parent_run_id: number | null; model: string | null;
  input: string; output: string; status: "running" | "ok" | "error"; error: string | null;
  tool_calls: { name: string; input: Json; is_error: boolean; output_preview: string }[];
  usage: { input_tokens?: number; output_tokens?: number; cache_read_input_tokens?: number; cache_creation_input_tokens?: number };
  started_at: string; finished_at: string | null;
};

// USD per million tokens: [input, output, cache read]. Estimates only; check current pricing.
const PRICES: Record<string, [number, number, number]> = {
  "claude-fable-5-2": [8, 40, 0.2],
  "claude-opus-5-5": [4, 20, 0.2],
  "claude-sonnet-5-5": [2, 10, 0.1],
  "claude-haiku-5-5": [0.1, 0.5, 0.01],
};

export function runCost(r: Run): number | null {
  const p = r.model ? PRICES[r.model] : undefined;
  if (!p) return null;
  const u = r.usage ?? {};
  return ((u.input_tokens ?? 0) * p[0] + (u.cache_creation_input_tokens ?? 0) * p[0] * 1.25
    + (u.cache_read_input_tokens ?? 0) * p[2] + (u.output_tokens ?? 0) * p[1]) / 1e6;
}

const k = (n?: number) => (!n ? "0" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const parseTime = (s: string) => new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z").getTime();
const secs = (r: Run) => ((r.finished_at ? parseTime(r.finished_at) : Date.now()) - parseTime(r.started_at)) / 1000;
const shortModel = (m: string | null) => (m ?? "?").replace("claude-", "");

function Stats({ r }: { r: Run }) {
  const cost = runCost(r);
  const u = r.usage ?? {};
  return (
    <span className="muted">
      {shortModel(r.model)} · {secs(r).toFixed(1)}s
      {r.status !== "running" && <> · {k((u.input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0))} in / {k(u.output_tokens)} out</>}
      {cost != null && r.status !== "running" && <> · ${cost.toFixed(4)}</>}
    </span>
  );
}

function Status({ r }: { r: Run }) {
  const label = { running: "running…", ok: "ok", error: "error" }[r.status];
  return <span className={`status ${r.status}`}>{label}</span>;
}

function ToolCalls({ calls }: { calls: Run["tool_calls"] }) {
  if (!calls?.length) return null;
  return (
    <div className="trace-tools">
      {calls.map((c, i) => (
        <details key={i} className={c.is_error ? "tool-error" : ""}>
          <summary>🔧 {c.name}{c.is_error && " (failed)"}</summary>
          <pre>{JSON.stringify(c.input, null, 2)}</pre>
          <pre className="preview">{c.output_preview}</pre>
        </details>
      ))}
    </div>
  );
}

function RunBody({ r }: { r: Run }) {
  return (
    <>
      <div className="trace-label">Asked</div>
      <div className="trace-text">{r.input}</div>
      <ToolCalls calls={r.tool_calls} />
      {r.error && <div className="error">{r.error}</div>}
      {r.output && (
        <details>
          <summary>Output ({k(r.output.length)} chars)</summary>
          <div className="trace-output"><ReactMarkdown remarkPlugins={[remarkGfm]}>{r.output}</ReactMarkdown></div>
        </details>
      )}
    </>
  );
}

export default function Trace({ sessionId, live, roleNames, onClose }:
  { sessionId: number | null; live: boolean; roleNames: Record<string, string>; onClose: () => void }) {
  const [runs, setRuns] = useState<Run[]>([]);

  useEffect(() => {
    if (sessionId == null) { setRuns([]); return; }
    let stop = false;
    const load = () => api.get<Run[]>(`/sessions/${sessionId}/runs`).then((r) => !stop && setRuns(r));
    load();
    // Poll while a turn is running so specialists appear as they start and finish.
    const t = live ? setInterval(load, 1500) : undefined;
    return () => { stop = true; if (t) clearInterval(t); };
  }, [sessionId, live]);

  const turns = runs.filter((r) => r.parent_run_id == null);
  const children = (id: number) => runs.filter((r) => r.parent_run_id === id);
  const total = runs.reduce((s, r) => s + (runCost(r) ?? 0), 0);
  const name = (slug: string) => roleNames[slug] ?? slug;

  return (
    <aside className="trace">
      <div className="trace-head">
        <strong>Trace</strong>
        <span className="muted">{turns.length} turns · {runs.length - turns.length} consults · est. ${total.toFixed(3)}</span>
        <button className="link" onClick={onClose}>✕</button>
      </div>
      {turns.length === 0 && <p className="muted">No runs yet. Ask a question to see how the team works on it.</p>}
      {turns.map((t, i) => (
        <details key={t.id} className="trace-turn" open={i === turns.length - 1}>
          <summary>
            <span>Turn {i + 1}: {name(t.role_slug)} <Status r={t} /></span>
            <Stats r={t} />
          </summary>
          <RunBody r={t} />
          {children(t.id).map((c) => (
            <details key={c.id} className="trace-child">
              <summary>
                <span>↳ {name(c.role_slug)} <Status r={c} /></span>
                <Stats r={c} />
              </summary>
              <RunBody r={c} />
            </details>
          ))}
        </details>
      ))}
    </aside>
  );
}
