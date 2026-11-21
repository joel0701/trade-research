/** "Suggested next steps" at the end of an answer, shown as clickable follow-ups with a rough
 * token and cost estimate (latest answer only). */
import { useEffect, useState } from "react";
import { api } from "./api";
import { ArrowUp } from "./icons";

const HEADING = /^#{2,4}\s*Suggested next steps?\s*:?\s*$/im;

type Step = { tokens: number; input: number; output: number; cost: number | null; cost_low: number | null; cost_high: number | null };
type Estimate = { model: string; basis: string; free: boolean; steps: Step[] };

const tok = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
const usd = (n: number) => (n < 0.01 ? "<$0.01" : `$${n.toFixed(2)}`);

/** Split an answer into its body and the suggested follow-up questions (if it ends with that section). */
export function splitNextSteps(text: string): { body: string; steps: string[] } {
  const m = HEADING.exec(text);
  if (!m) return { body: text, steps: [] };
  const rest = text.slice(m.index + m[0].length);
  const steps = rest.split("\n").map((l) => l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/)?.[1]?.trim())
    .filter((s): s is string => !!s)
    .map((s) => s.replace(/^\*\*(.*)\*\*$/, "$1").replace(/^["“](.*)["”]$/, "$1"));
  if (!steps.length) return { body: text, steps: [] };
  return { body: text.slice(0, m.index).trimEnd(), steps };
}

/** sessionId: when given, each step shows an estimate of its tokens and cost. */
export default function NextSteps({ steps, onPick, sessionId }: { steps: string[]; onPick: (q: string) => void; sessionId?: number }) {
  const [est, setEst] = useState<Estimate | null>(null);
  const key = steps.join("\n");
  useEffect(() => {
    setEst(null);
    if (sessionId == null) return;
    let live = true;
    api.post(`/sessions/${sessionId}/estimate`, { questions: steps })
      .then((e: Estimate) => { if (live) setEst(e); }).catch(() => {});
    return () => { live = false; };
  }, [sessionId, key]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="next-steps tone-purple">
      <div className="next-steps-head"><strong>Suggested next steps</strong>
        <span className="muted small">click to put it in the box, then edit or send</span>
        {est && <span className="muted small next-steps-model" title={est.basis}>estimates for {est.model} ⓘ</span>}</div>
      {steps.map((s, i) => {
        const e = est?.steps[i];
        const range = e && e.cost != null && !est!.free ? `; likely ${usd(e.cost_low!)} to ${usd(e.cost_high!)}` : "";
        return (
          <button key={i} className="next-step" onClick={() => onPick(s)} title="Put this question in the box">
            <span>{s}</span>
            {e && (
              <span className="next-step-est"
                    title={`About ${e.input.toLocaleString()} input and ${e.output.toLocaleString()} output tokens${range}.\n${est!.basis}`}>
                ~{tok(e.tokens)} tokens · {est!.free ? "free" : e.cost != null ? `~${usd(e.cost)}` : "cost n/a"}
              </span>
            )}
            <span className="next-step-go" aria-hidden="true"><ArrowUp /></span>
          </button>
        );
      })}
    </div>
  );
}
