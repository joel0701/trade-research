/** Under each answer: model, time and cost details, then actions (save as skill, fork, copy, rate). */
import { useState } from "react";
import { api } from "./api";
import { Check, Copy, Fork, ModelMark, Sparkles, ThumbDown, ThumbUp } from "./icons";
import { ModelInfo, VENDOR_COLORS } from "./models";
import { parseTime, Run, runCost } from "./Trace";
import { useSettings } from "./settings";

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;


export default function AnswerFooter({ sessionId, message, models, runs, roleNames, onViewTrace, onSaveSkill,
                                       onFork, onError }: {
  sessionId: number;
  message: { id?: number; text: string; model?: string | null; feedback?: string | null; run_id?: number | null; created_at?: string };
  models: ModelInfo[];
  runs: Run[];
  roleNames: Record<string, string>;
  onViewTrace: (runId: number) => void;
  onSaveSkill: () => void;
  onFork: (messageId: number) => void;
  onError: (msg: string) => void;
}) {
  const { fmt } = useSettings();
  const [feedback, setFeedback] = useState<string | null>(message.feedback ?? null);
  const [copied, setCopied] = useState(false);
  const model = models.find((m) => m.config?.model_id === message.model);
  const modelName = (model?.name ?? message.model ?? "").replace(/^Claude /, "");

  const lead = message.run_id != null ? runs.find((r) => r.id === message.run_id) : undefined;
  const kids = lead ? runs.filter((r) => r.parent_run_id === lead.id) : [];
  const costs = lead ? [lead, ...kids].map(runCost) : [];
  const cost = costs.reduce<number>((s, c) => s + (c ?? 0), 0);
  const priced = costs.some((c) => c != null);
  const costLabel = priced ? `est. $${cost.toFixed(3)}${costs.some((c) => c == null) ? "+" : ""}` : "cost n/a";
  const took = lead?.finished_at ? (parseTime(lead.finished_at) - parseTime(lead.started_at)) / 1000 : null;
  const names = [...new Set(kids.map((k) => roleNames[k.role_slug] ?? k.role_slug))];
  const failed = lead ? [lead, ...kids].some((r) => r.status === "error") : false;

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { onError("Couldn't copy: the browser blocked clipboard access."); }
  }

  async function rate(r: "up" | "down") {
    if (message.id == null) return;
    const before = feedback;
    const next = feedback === r ? null : r; // clicking the same thumb again clears it
    setFeedback(next);
    try { await api.put(`/sessions/${sessionId}/messages/${message.id}/feedback`, { rating: next }); }
    catch (e: any) { setFeedback(before); onError(e.message); }
  }

  return (
    <div className="answer-footer">
      <div className="answer-meta">
        {message.model && (
          <span className="model-tag" title={message.model}>
            <ModelMark color={VENDOR_COLORS[model?.provider ?? "anthropic"]} /> {modelName}
          </span>
        )}
        {message.created_at && <span>{fmt(message.created_at)}</span>}
        {took != null && <span>Answered in {clock(took)}</span>}
        {lead && <span>{names.length ? names.join(", ") : "No specialists consulted"}</span>}
        {lead && <span>{costLabel}</span>}
        {failed && <span className="error">some steps failed</span>}
        {lead && <button className="link" onClick={() => onViewTrace(lead.id)}>View in trace</button>}
      </div>
      <div className="answer-actions">
        <button className="act act-accent" onClick={onSaveSkill}><Sparkles /> Save as skill</button>
        {message.id != null && <button className="act" onClick={() => onFork(message.id!)}><Fork /> Fork chat</button>}
        <button className="act icon" onClick={copy} title={copied ? "Copied" : "Copy response"} aria-label="Copy response">
          {copied ? <Check /> : <Copy />}
        </button>
        <button className={`act icon ${feedback === "up" ? "on" : ""}`} onClick={() => rate("up")}
                title="Good response" aria-label="Good response" aria-pressed={feedback === "up"}>
          <ThumbUp filled={feedback === "up"} />
        </button>
        <button className={`act icon ${feedback === "down" ? "on" : ""}`} onClick={() => rate("down")}
                title="Bad response" aria-label="Bad response" aria-pressed={feedback === "down"}>
          <ThumbDown filled={feedback === "down"} />
        </button>
      </div>
    </div>
  );
}
