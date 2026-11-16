/** Shown instead of running a question when data sources assigned to roles are unavailable. */
import { Link } from "react-router-dom";
import { UnavailableItem, UnavailableReport } from "./api";

/** Items grouped by cause, so one missing key or stopped app is listed once with everything it affects. */
function ByCause({ items, detail }: { items: UnavailableItem[]; detail: (i: UnavailableItem) => string }) {
  const groups = new Map<string, UnavailableItem[]>();
  for (const i of items) groups.set(i.reason ?? "Unavailable", [...(groups.get(i.reason ?? "Unavailable") ?? []), i]);
  return (
    <ul className="unavail-list">
      {[...groups.entries()].map(([cause, list]) => (
        <li key={cause}>
          <span className="unavail-cause">{cause.charAt(0).toUpperCase() + cause.slice(1)}</span>
          <ul>
            {list.map((i) => (
              <li key={`${i.role_slug}-${i.tool_id}`}>
                <strong>{i.role}</strong> · {i.tool} <span className="muted">· {detail(i)}</span>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

export default function UnavailablePrompt({ question, report, onContinue, onCancel }: {
  question: string;
  report: UnavailableReport;
  onContinue: (rememberForSession: boolean) => void;
  onCancel: () => void;
}) {
  const high = report.items.filter((i) => i.impact === "high");
  const low = report.items.filter((i) => i.impact === "low");

  return (
    <div className="msg unavailable" role="alertdialog" aria-label="Some data sources are unavailable">
      <div className="unavail-head">
        <strong>Some data sources are unavailable</strong>
        <span className="muted small">{report.summary}</span>
      </div>
      <p className="small">
        Your question hasn't been sent yet: <em>"{question.length > 140 ? question.slice(0, 140) + "…" : question}"</em>.
        Decide whether the analysis still makes sense without these sources.
      </p>

      {high.length > 0 && (
        <>
          <div className="unavail-group high">High impact: no other source for this data</div>
          <ByCause items={high} detail={(i) => `no ${i.gaps.join(", ")}`} />
        </>
      )}
      {low.length > 0 && (
        <details open={high.length === 0}>
          <summary className="unavail-group low">Low impact: another source covers it ({low.length})</summary>
          <ByCause items={low} detail={(i) => `covered by ${i.covered_by.join(", ")}`} />
        </details>
      )}

      <div className="answer-actions">
        <button className="primary" onClick={() => onContinue(false)}>Continue anyway</button>
        <button onClick={() => onContinue(true)} title="Only for this session, and only while the same sources are unavailable">
          Continue, don't ask again in this session
        </button>
        <button onClick={onCancel}>Cancel</button>
        <Link to="/tools" className="unavail-link">Set up sources in Tools</Link>
      </div>
    </div>
  );
}
