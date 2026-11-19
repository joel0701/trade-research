/** Chat composer: text box with a toolbar for model choice, attachments and skills. */
import { useEffect, useRef, useState } from "react";
import { AttachmentInfo, Json, uploadAttachment } from "./api";
import { ArrowUp, Check, Chevron, Close, FileIcon, ModelMark, Paperclip, Sparkles } from "./icons";
import { byVendor, ModelInfo, VENDOR_COLORS } from "./models";

export type ModelTool = ModelInfo;

type Pending = { key: string; name: string; info?: AttachmentInfo; error?: string };

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.md,.markdown,.csv,.tsv,.json,.xml,.html,.htm,.log,.yaml,.yml";

export function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", key); };
  }, [open]);
  return { open, setOpen, ref };
}

function SkillsMenu({ skills, onRun }: { skills: Json[]; onRun: (skill: Json, params: Record<string, string>) => Promise<void> }) {
  const pop = usePopover();
  const [chosen, setChosen] = useState<Json | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);

  function pick(s: Json) {
    setChosen(s);
    setError("");
    setParams(Object.fromEntries(s.params.map((p: Json) => [p.name, p.default ?? ""])));
  }
  async function run() {
    if (!chosen) return;
    setRunning(true); setError("");
    try { await onRun(chosen, params); pop.setOpen(false); setChosen(null); }
    catch (e: any) { setError(e.message); }
    finally { setRunning(false); }
  }

  return (
    <div className="pop-anchor" ref={pop.ref}>
      <button type="button" className="tool-btn" onClick={() => { pop.setOpen(!pop.open); setChosen(null); }}>
        <Sparkles /> <span className="tool-label">Skills</span>
      </button>
      {pop.open && (
        <div className="popover up">
          {!chosen ? (
            <>
              <div className="pop-title">Run a skill <span className="muted">· opens a new session</span></div>
              {skills.length === 0 && <div className="muted pop-item">No skills enabled.</div>}
              {skills.map((s) => (
                <button key={s.id} className="pop-item" onClick={() => pick(s)}>
                  <strong>{s.name}</strong>
                  <span className="muted">{s.description}</span>
                </button>
              ))}
            </>
          ) : (
            <form className="skill-form" onSubmit={(e) => { e.preventDefault(); run(); }}>
              <div className="pop-title">
                <button type="button" className="link" onClick={() => setChosen(null)}>←</button> {chosen.name}
              </div>
              {chosen.params.map((p: Json, i: number) => (
                <label key={p.name}>{p.label ?? p.name}
                  <input autoFocus={i === 0} value={params[p.name] ?? ""}
                         onChange={(e) => setParams({ ...params, [p.name]: e.target.value })} />
                </label>
              ))}
              {error && <div className="error">{error}</div>}
              <button className="primary" disabled={running}>{running ? "Starting…" : "Run skill"}</button>
              <div className="muted small">Uses the model selected in the composer.</div>
            </form>
          )}
        </div>
      )}
    </div>
  );
}

export default function Composer({ placeholder, busy, models, modelId, defaultModelId, onModelChange,
                                   ensureSession, onSend, skills, onRunSkill, restore, suggestion, onOpenModels }: {
  placeholder: string;
  busy: boolean;
  models: ModelTool[];
  modelId: number | null;          // the session's choice (null = Lead role default)
  defaultModelId: number | null;   // the Lead role's model
  onModelChange: (id: number | null) => void;
  ensureSession: () => Promise<number>; // uploads need a session; creates one if needed
  onSend: (text: string, attachments: AttachmentInfo[]) => void;
  skills: Json[];
  onRunSkill: (skill: Json, params: Record<string, string>) => Promise<void>;
  restore?: { text: string; nonce: number }; // put text back in the box (e.g. after Cancel)
  suggestion?: string;  // shown as grey text in the empty box; Tab fills it in, typing replaces it
  onOpenModels?: () => void;  // refresh the model list (e.g. a local model server was just started)
}) {
  const [text, setText] = useState("");
  useEffect(() => { if (restore) { setText(restore.text); box.current?.focus(); } }, [restore?.nonce]);
  const [files, setFiles] = useState<Pending[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const modelPop = usePopover();

  const ghost = !text && suggestion && !busy ? suggestion : "";  // the suggestion shows only while the box is empty
  const usable = models.filter((m) => m.available);
  const effective = models.find((m) => m.id === (modelId ?? defaultModelId));
  const uploading = files.some((f) => !f.info && !f.error);
  const ready = files.filter((f) => f.info);
  const canSend = !busy && !uploading && (text.trim().length > 0 || ready.length > 0);

  // Grow the text box with its content, up to a limit.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  async function addFiles(list: FileList | File[]) {
    const arr = Array.from(list);
    if (!arr.length) return;
    const entries = arr.map((f) => ({ key: `${Date.now()}-${Math.random()}`, name: f.name }));
    setFiles((cur) => [...cur, ...entries]);
    let sid: number;
    try { sid = await ensureSession(); } catch (e: any) {
      setFiles((cur) => cur.map((p) => entries.some((x) => x.key === p.key) ? { ...p, error: e.message } : p));
      return;
    }
    await Promise.all(arr.map(async (f, i) => {
      const key = entries[i].key;
      try {
        const info = await uploadAttachment(sid, f);
        setFiles((cur) => cur.map((p) => (p.key === key ? { ...p, info } : p)));
      } catch (e: any) {
        setFiles((cur) => cur.map((p) => (p.key === key ? { ...p, error: e.message } : p)));
      }
    }));
  }

  function removeFile(p: Pending) {
    setFiles((cur) => cur.filter((x) => x.key !== p.key));
    // Unsent uploads are cleaned up on the server too (best effort).
    if (p.info) ensureSession().then((sid) => fetch(`/api/sessions/${sid}/attachments/${p.info!.id}`, { method: "DELETE" })).catch(() => {});
  }

  function submit() {
    if (!canSend) return;
    onSend(text, ready.map((f) => f.info!));
    setText("");
    setFiles([]);
  }

  return (
    <div className={`composer-card ${dragging ? "dragging" : ""}`}
         onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
         onDragLeave={() => setDragging(false)}
         onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}>
      {files.length > 0 && (
        <div className="chips">
          {files.map((f) => (
            <span key={f.key} className={`chip ${f.error ? "chip-error" : ""}`} title={f.error ?? f.name}>
              {f.info || f.error ? <FileIcon /> : <span className="spinner small" />}
              <span className="chip-name">{f.name}</span>
              {f.error && <span className="chip-err">{f.error}</span>}
              <button type="button" className="chip-x" aria-label={`Remove ${f.name}`} onClick={() => removeFile(f)}><Close /></button>
            </span>
          ))}
        </div>
      )}
      <div className="ghost-wrap" onClick={() => box.current?.focus()}>
        <textarea ref={box} rows={1} value={text} placeholder={ghost ? "" : placeholder}
                  aria-label={ghost ? `Message. Suggested: ${ghost}. Press Tab to use it.` : placeholder}
                  onChange={(e) => setText(e.target.value)}
                  onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }}
                  onKeyDown={(e) => {
                    if (e.key === "Tab" && !e.shiftKey && ghost) { e.preventDefault(); setText(ghost); return; }
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
                  }} />
        {ghost && (
          <div className="ghost" aria-hidden="true">{ghost} <kbd>Tab</kbd></div>
        )}
      </div>
      <div className="composer-bar">
        <div className="pop-anchor" ref={modelPop.ref}>
          <button type="button" className="tool-btn model-btn" onClick={() => { if (!modelPop.open) onOpenModels?.(); modelPop.setOpen(!modelPop.open); }}
                  title="Model the Lead Analyst uses for this session">
            <ModelMark color={VENDOR_COLORS[effective?.provider ?? ""]} />
            <strong className="model-name">{effective?.name.replace(/^Claude /, "") ?? "Model"}</strong>
            {effective && !effective.available && <span className="error small">no API key</span>}
            <Chevron />
          </button>
          {modelPop.open && (
            <div className="popover up">
              <div className="pop-title">Lead Analyst model <span className="muted">· this session</span></div>
              {usable.length === 0 && <div className="pop-item muted">No models available. Add an API key to .env, or start a local model server and add its models in Tools → Models.</div>}
              {byVendor(usable).map(([vendor, list]) => (
                <div key={vendor}>
                  <div className="pop-group">{vendor}</div>
                  {list.map((m) => {
                    const selected = m.id === (modelId ?? defaultModelId);
                    return (
                      <button key={m.id} className="pop-item row" onClick={() => {
                        onModelChange(m.id === defaultModelId ? null : m.id);
                        modelPop.setOpen(false);
                      }}>
                        <span className="inline-start">
                          <ModelMark color={VENDOR_COLORS[m.provider]} />
                          <span>
                            <strong>{m.name}</strong>{m.id === defaultModelId && <span className="badge">default</span>}
                            <span className="muted block">{m.description || m.config.model_id}</span>
                          </span>
                        </span>
                        {selected && <Check />}
                      </button>
                    );
                  })}
                </div>
              ))}
              <div className="muted small pop-foot">Only vendors with an API key in .env are listed. Specialists keep the models set in Roles.</div>
            </div>
          )}
        </div>
        <button type="button" className="tool-btn" onClick={() => fileInput.current?.click()}><Paperclip /> <span className="tool-label">Attach</span></button>
        <input ref={fileInput} type="file" multiple accept={ACCEPT} hidden
               onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        <SkillsMenu skills={skills} onRun={onRunSkill} />
        <span className="spacer" />
        <button type="button" className="send-btn" disabled={!canSend} onClick={submit}
                aria-label="Send" title={uploading ? "Waiting for uploads…" : "Send (Enter)"}><ArrowUp /></button>
      </div>
    </div>
  );
}
