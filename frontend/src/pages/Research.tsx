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
  // True while the session on screen has a turn in progress, whether this page started it
  // or it was started earlier (e.g. before you switched to another screen).
  const [busy, setBusy] = useState(false);
  const [liveText, setLiveText] = useState(false); // the answer is streaming in right now
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState("");
  const viewing = useRef<number | null>(sessionId);
  viewing.current = sessionId;
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

  // While a turn runs: refresh the trace, tick the clock, and notice when it finishes.
  // This works whether or not this page is the one streaming the answer.
  useEffect(() => {
    if (!busy || sessionId == null) return;
    const sid = sessionId;
    let checks = 0;
    const t = setInterval(async () => {
      setNow(Date.now());
      loadRuns(sid);
      if (++checks % 2) return; // check the session itself every 2s
      try {
        const s = await api.get(`/sessions/${sid}`);
        if (!s.running && viewing.current === sid) {
          setMessages(s.messages);
          setBusy(false);
          setLiveText(false);
          loadRuns(sid);
          loadSessions();
          showLatestAnswer();
        }
      } catch { /* keep polling */ }
    }, 1000);
    return () => clearInterval(t);
  }, [busy, sessionId]);

  // Keep the sidebar's "working" indicators current while any session is running.
  const anyRunning = sessions.some((s) => s.running);
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(loadSessions, 3000);
    return () => clearInterval(t);
  }, [anyRunning]);

  /** Scroll so the top of the latest answer is visible (not the bottom of it). */
  function showLatestAnswer() {
    requestAnimationFrame(() => {
      if (answerRef.current) answerRef.current.scrollIntoView({ block: "start", behavior: "smooth" });
      else scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
    });
  }

  useEffect(() => {
    setError(""); setFocusRunId(null); setRuns([]); setBusy(false); setLiveText(false);
    if (sessionId == null) { setMessages([]); return; }
    try { localStorage.setItem("lastSession", String(sessionId)); } catch { /* storage unavailable */ }
    loadRuns(sessionId);
    api.get(`/sessions/${sessionId}`).then((s) => {
      if (viewing.current !== sessionId) return;
      setMessages(s.messages);
      setBusy(!!s.running); // a turn started earlier is still going: show its status
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
    setError(""); setDraft(""); setFocusRunId(null);
    try {
      if (sid == null) {
        const s = await api.post("/sessions", {});
        navigate(`/research/${s.id}`, { state: { autoSend: text } });
        return; // the effect for the new session sends it
      }
      const target = sid;
      setBusy(true); setNow(Date.now());
      setMessages((m) => [...m, { role: "user", text }]);
      requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));

      let startNewAnswer = true; // the Lead's text before a tool call is an interim note: replace it
      let scrolledToAnswer = false;
      let detached = false; // the user opened another session: stop drawing this stream
      await sendMessage(target, text, (e: StreamEvent) => {
        if (viewing.current !== target) detached = true;
        if (detached) return;
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
          if (e.type === "text") {
            setLiveText(true);
            if (!scrolledToAnswer) { scrolledToAnswer = true; showLatestAnswer(); }
          }
        } else if (e.type === "tool_call" && e.source === "lead") {
          startNewAnswer = true;
          scrolledToAnswer = false;
          setLiveText(false);
        } else if (e.type === "error") {
          setError(e.message);
        }
      });
      loadSessions();
      if (viewing.current === target) {
        // Reload from the server so the view matches exactly what was saved.
        const s = await api.get(`/sessions/${target}`);
        if (viewing.current === target) {
          setMessages(s.messages);
          setBusy(!!s.running);
          setLiveText(false);
          await loadRuns(target);
        }
      }
    } catch (err: any) {
      if (viewing.current === sid) { setError(String(err.message ?? err)); setBusy(false); setLiveText(false); }
    }
  }

  function onDeleted(sid: number) {
    try { if (localStorage.getItem("lastSession") === String(sid)) localStorage.removeItem("lastSession"); } catch { /* ignore */ }
    if (sid === sessionId) navigate("/research");
    loadSessions();
  }

  function viewInTrace(runId: number) {
    toggleTrace(true);
    setFocusRunId(runId);
  }

  const lastAssistant = messages.map((m) => m.role).lastIndexOf("assistant");
  // Status comes from the trace, so it is right even after leaving and coming back.
  const leadRun = [...runs].reverse().find((r) => r.parent_run_id == null && r.status === "running");
  const working = leadRun
    ? [...new Set(runs.filter((r) => r.parent_run_id === leadRun.id && r.status === "running")
        .map((r) => roleNames[r.role_slug] ?? r.role_slug))]
    : [];
  const elapsed = leadRun ? Math.max(0, (now - parseTime(leadRun.started_at)) / 1000) : 0;

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
              {liveText ? "Writing the answer" : working.length ? `Consulting ${working.join(", ")}` : "Lead Analyst is working"}
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
