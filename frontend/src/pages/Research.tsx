import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, sendMessage, StreamEvent } from "../api";
import Trace from "../Trace";
import SessionList, { SessionRow } from "../SessionList";

type ChatMsg = { id?: number; role: "user" | "assistant"; text: string; thinking?: string };

function Markdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>;
}

export default function Research() {
  const { id } = useParams();
  const sessionId = id ? Number(id) : null;
  const navigate = useNavigate();
  const location = useLocation();
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [activity, setActivity] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const autoSent = useRef<number | null>(null);
  const [showTrace, setShowTrace] = useState(() => {
    try { return localStorage.getItem("showTrace") === "1"; } catch { return false; }
  });
  const [roleNames, setRoleNames] = useState<Record<string, string>>({});
  useEffect(() => {
    api.get("/roles").then((rs) => setRoleNames(Object.fromEntries(rs.map((r: any) => [r.slug, r.name]))));
  }, []);
  function toggleTrace(v: boolean) {
    setShowTrace(v);
    try { localStorage.setItem("showTrace", v ? "1" : "0"); } catch { /* storage unavailable */ }
  }

  const loadSessions = () => api.get<SessionRow[]>("/sessions").then(setSessions)
    .catch((e) => setError(e.message));
  useEffect(() => { loadSessions(); }, []);

  useEffect(() => {
    setActivity([]); setError("");
    if (sessionId == null) { setMessages([]); return; }
    api.get(`/sessions/${sessionId}`).then((s) => {
      setMessages(s.messages);
      // Skills and screeners open a new session with a prompt to send right away.
      const auto = (location.state as any)?.autoSend as string | undefined;
      if (auto && s.messages.length === 0 && autoSent.current !== sessionId) {
        autoSent.current = sessionId;
        navigate(location.pathname, { replace: true, state: null });
        send(auto, sessionId);
      }
    }).catch((e) => setError(e.message));
  }, [sessionId]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, activity]);

  async function send(text: string, sid: number | null = sessionId) {
    if (!text.trim() || busy) return;
    setBusy(true); setError(""); setDraft(""); setActivity([]);
    try {
      if (sid == null) {
        const s = await api.post("/sessions", {});
        sid = s.id as number;
        navigate(`/research/${sid}`, { state: { autoSend: text } });
        setBusy(false);
        return; // the effect for the new session sends it
      }
      setMessages((m) => [...m, { role: "user", text }, { role: "assistant", text: "", thinking: "" }]);
      await sendMessage(sid, text, (e: StreamEvent) => {
        if (e.type === "text" || e.type === "thinking") {
          setMessages((m) => {
            const copy = [...m];
            const last = { ...copy[copy.length - 1] };
            if (e.type === "text") last.text += e.text; else last.thinking = (last.thinking ?? "") + e.text;
            copy[copy.length - 1] = last;
            return copy;
          });
        } else if (e.type === "specialist_start") {
          setActivity((a) => [...a, `→ ${e.name}: ${e.question}`]);
        } else if (e.type === "specialist_end") {
          setActivity((a) => [...a, `✓ ${e.name} reported back`]);
        } else if (e.type === "tool_call") {
          setActivity((a) => [...a, `   ${e.source} used ${e.name}`]);
        } else if (e.type === "error") {
          setError(e.message);
        }
      });
      // Reload from the server so the view matches exactly what was saved.
      const s = await api.get(`/sessions/${sid}`);
      setMessages(s.messages);
      loadSessions();
    } catch (err: any) {
      setError(String(err.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  function onDeleted(sid: number) {
    if (sid === sessionId) navigate("/research");
    loadSessions();
  }

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
        <div className="messages">
          {messages.length === 0 && !busy && (
            <div className="msg muted">Ask about the market, a sector or a stock. The Lead Analyst will consult the team as needed.</div>
          )}
          {messages.map((m, i) => (
            <div key={m.id ?? `live-${i}`} className={`msg ${m.role}`}>
              {m.thinking && <details><summary>Reasoning</summary><Markdown text={m.thinking} /></details>}
              {m.role === "assistant" && !m.text && busy && i === messages.length - 1
                ? <span className="muted">Working…</span>
                : <Markdown text={m.text} />}
            </div>
          ))}
          {activity.length > 0 && <div className="activity">{activity.map((a, i) => <div key={i}>{a}</div>)}</div>}
          {error && <div className="msg error">{error}</div>}
          <div ref={bottom} />
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
      {showTrace && <Trace sessionId={sessionId} live={busy} roleNames={roleNames} onClose={() => toggleTrace(false)} />}
    </div>
  );
}
