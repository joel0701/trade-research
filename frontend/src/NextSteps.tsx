/** "Suggested next steps" at the end of a skill or scanner answer, shown as clickable follow-ups. */
import { ArrowUp } from "./icons";

const HEADING = /^#{2,4}\s*Suggested next steps?\s*:?\s*$/im;

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

export default function NextSteps({ steps, onPick }: { steps: string[]; onPick: (q: string) => void }) {
  return (
    <div className="next-steps tone-purple">
      <div className="next-steps-head"><strong>Suggested next steps</strong>
        <span className="muted small">click to put it in the box, then edit or send</span></div>
      {steps.map((s, i) => (
        <button key={i} className="next-step" onClick={() => onPick(s)} title="Put this question in the box">
          <span>{s}</span><span className="next-step-go" aria-hidden="true"><ArrowUp /></span>
        </button>
      ))}
    </div>
  );
}
