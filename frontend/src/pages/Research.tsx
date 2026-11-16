import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, sendMessage, StreamEvent } from "../api";
import Trace, { parseTime, Run, runCost } from "../Trace";
import SessionList, { SessionRow } from "../SessionList";

type ChatMsg = { id?: number; role: "user" | "assistant"; text: string; thinking?: string; run_id?: number | null };

function Markdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>;
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** "Answered in 1:12 · Technical Analyst, News Analyst · est. $0.084 · View in trace" */
function TurnSummary({ runId, runs, roleNames, onView }:
  { runId: number; runs: Run[]; roleNames: Record<string, string>; onView: () => void }) {
  const lead = runs.find((r) => r.id === runId);
  if (!lead) return null;
  const kids = runs.filter((r) => r.parent_run_id === runId);
  const cost = [lead, ...kids].reduce((s, r) => s + (runCost(r) ?? 0), 0);
  const took = lead.finished_at ? (parseTime(lead.finished_at) - parseTime(lead.started_at)) / 1000 : null;
  const names = [...new Set(kids.map((k) => roleNames[k.role_slug] ?? k.role_slug))];
  const failed = [lead, ...kids].some((r) => r.status === "error");
  return (
    <div className="turn-summary">
      {took != null && <span>Answered in {clock(took)}</span>}
      <span>{names.length ? names.join(", ") : "No specialists consulted"}</span>
      <span>est. ${cost.toFixed(3)}</span>
      {failed && <span className="error">some steps failed</span>}
      <button className="link" onClick={onView}>View in trace</button>
    </div>
  );
}

export default function Research() {
  const { id } = useParams();
  const sessionId = id ? Number(id) : null;
  const navigate = useNavigate();
  const location = useLocation();
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [working, setWorking] = useState<string[]>([]); // specialists currently consulted
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [focusRunId, setFocusRunId] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const answerRef = useRef<HTMLDivElement>(null);
  const autoSent = useRef<number | null>(null);
  const [showTrace, setShowTrace] = useState(() => {
    try { return localStorage.getItem("showTrace") !== "0"; } catch { return true; } // open by default
  });
  const [roleNames, setRoleNames] = useState<Record<string, string>>({});
  useEffect(() => {
    api.get("/roles").then((rs) => setRoleNames(Object.fromEntries(rs.map((r: any) => [r.slug, r.name]))))
      .catch(() => { /* names fall back to slugs */ });
  }, []);
  function toggleTrace(v: boolean) {
    setShowTrace(v);
    try { localStorage.setItem("showTrace", v ? "1" : "0"); } catch { /* storage unavailable */ }
  }

  const loadSessions = () => api.get<SessionRow[]>("/sessions").then(setSessions)
    .catch((e) => setError(e.message));
  const loadRuns = (sid: number) => api.get<Run[]>(`/sessions/${sid}/runs`).then(setRuns).catch(() => {});
  useEffect(() => { loadSessions(); }, []);

  // Poll the trace while a turn runs; tick the elapsed-time clock.
  useEffect(() => {
    if (!busy || sessionId == null) return;
    const start = Date.now();
    setElapsed(0);
    const t = setInterval(() => { setElapsed((Date.now() - start) / 1000); loadRuns(sessionId); }, 1000);
    return () => clearInterval(t);
  }, [busy, sessionId]);

  /** Scroll so the top of the latest answer is visible (not the bottom of it). */
  function showLatestAnswer() {
    requestAnimationFrame(() => {
      if (answerRef.current) answerRef.current.scrollIntoView({ block: "start", behavior: "smooth" });
      else scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
    });
  }

  useEffect(() => {
    setError(""); setFocusRunId(null); setRuns([]);
    if (sessionId == null) { setMessages([]); return; }
    loadRuns(sessionId);
    api.get(`/sessions/${sessionId}`).then((s) => {
      setMessages(s.messages);
      showLatestAnswer();
      // Skills and screeners open a new session with a prompt to send right away.
      const auto = (location.state as any)?.autoSend as string | undefined;
      if (auto && s.messages.length === 0 && autoSent.current !== sessionId) {
        autoSent.current = sessionId;
        navigate(location.pathname, { replace: true, state: null });
        send(auto, sessionId);
      }
    }).catch((e) => setError(e.message));
  }, [sessionId]);

  async function send(text: string, sid: number | null = sessionId) {
    if (!text.trim() || busy) return;
    setBusy(true); setError(""); setDraft(""); setWorking([]); setFocusRunId(null);
    try {
      if (sid == null) {
        const s = await api.post("/sessions", {});
        sid = s.id as number;
        navigate(`/research/${sid}`, { state: { autoSend: text } });
        setBusy(false);
        return; // the effect for the new session sends it
      }
      setMessages((m) => [...m, { role: "user", text }]);
      requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));

      let startNewAnswer = true; // the Lead's text before a tool call is an interim note: replace it
      let scrolledToAnswer = false;
      await sendMessage(sid, text, (e: StreamEvent) => {
        if ((e.type === "text" || e.type === "thinking") && e.source === "lead") {
          setMessages((m) => {
            const copy = [...m];
            if (startNewAnswer) {
              if (copy[copy.length - 1]?.role === "assistant") copy.pop();
              copy.push({ role: "assistant", text: "", thinking: "" });
              startNewAnswer = false;
            }
            const last = { ...copy[copy.length - 1] };
            if (e.type === "text") last.text += e.text; else last.thinking = (last.thinking ?? "") + e.text;
            copy[copy.length - 1] = last;
            return copy;
          });
          if (e.type === "text" && !scrolledToAnswer) { scrolledToAnswer = true; showLatestAnswer(); }
        } else if (e.type === "tool_call" && e.source === "lead") {
          startNewAnswer = true;
          scrolledToAnswer = false;
        } else if (e.type === "specialist_start") {
          setWorking((w) => [...w, e.name]);
        } else if (e.type === "specialist_end") {
          setWorking((w) => { const i = w.indexOf(e.name); return i < 0 ? w : [...w.slice(0, i), ...w.slice(i + 1)]; });
        } else if (e.type === "error") {
          setError(e.message);
        }
      });
      // Reload from the server so the view matches exactly what was saved.
      const s = await api.get(`/sessions/${sid}`);
      setMessages(s.messages);
      await loadRuns(sid);
      loadSessions();
    } catch (err: any) {
      setError(String(err.message ?? err));
    } finally {
      setBusy(false);
      setWorking([]);
    }
  }

  function onDeleted(sid: number) {
    if (sid === sessionId) navigate("/research");
    loadSessions();
  }

  function viewInTrace(runId: number) {
    toggleTrace(true);
    setFocusRunId(runId);
  }

  const lastAssistant = messages.map((m) => m.role).lastIndexOf("assistant");
  const live = busy && messages[messages.length - 1]?.role === "assistant";

  return (
    <div className="research">
      <aside className="sidebar">
        <button className="primary" style={{ width: "100%", marginBottom: 8 }} onClick={() => navigate("/research")}>
          + New research
        </button>
        <SessionList sessions={sessions} activeId={sessionId}
                     onOpen={(sid) => navigate(`/research/${sid}`)}
                     onChanged={loadSessions} onDeleted={onDeleted} onError={setError} />
      </aside>
      <section className="chat">
        <div className="chat-head">
          <span className="title">{sessions.find((s) => s.id === sessionId)?.title ?? "New research"}</span>
          <button onClick={() => toggleTrace(!showTrace)}>{showTrace ? "Hide trace" : "Show trace"}</button>
        </div>
        <div className="messages" ref={scroller}>
          {messages.length === 0 && !busy && (
            <div className="msg muted">Ask about the market, a sector or a stock. The Lead Analyst will consult the team as needed.</div>
          )}
          {messages.map((m, i) => (
            <div key={m.id ?? `live-${i}`} ref={i === lastAssistant ? answerRef : undefined} className={`msg ${m.role}`}>
              {m.thinking && <details><summary>Reasoning</summary><Markdown text={m.thinking} /></details>}
              <Markdown text={m.text} />
              {m.role === "assistant" && m.run_id != null && !busy && (
                <TurnSummary runId={m.run_id} runs={runs} roleNames={roleNames} onView={() => viewInTrace(m.run_id!)} />
              )}
            </div>
          ))}
          {busy && (
            <div className="status-line">
              <span className="spinner" />
              {live ? "Writing the answer" : working.length ? `Consulting ${working.join(", ")}` : "Lead Analyst is working"}
              <span className="muted"> · {clock(elapsed)}</span>
            </div>
          )}
          {error && <div className="msg error">{error}</div>}
        </div>
        <div className="composer">
          <textarea
            value={draft}
            placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(draft); } }}
          />
          <button className="primary" disabled={busy || !draft.trim()} onClick={() => send(draft)}>Send</button>
        </div>
      </section>
      {showTrace && sessionId != null && (
        <Trace runs={runs} roleNames={roleNames} focusRunId={focusRunId} onClose={() => toggleTrace(false)} />
      )}
    </div>
  );
}
