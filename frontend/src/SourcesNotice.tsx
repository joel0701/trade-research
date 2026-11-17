/** A record in the conversation of which data sources were unavailable and what the user decided. */
import { Json } from "./api";

const DECISIONS: Record<string, string> = {
  continued: "Continued without",
  continued_remember: "Continued (don't ask again in this session) without",
  remembered: "Ran without (per your earlier choice in this session)",
  cancelled: "Cancelled: question not sent. Unavailable:",
};

export default function SourcesNotice({ notice }: { notice: Json }) {
  const items: Json[] = notice.items ?? [];
  const high = items.filter((i) => i.impact === "high").length;
  const cancelled = notice.decision === "cancelled";
  const label = `${items.length} data source${items.length === 1 ? "" : "s"}`;
  return (
    <details className={`sources-notice ${cancelled ? "cancelled" : ""}`}>
      <summary>
        <span className="notice-icon" aria-hidden="true">{cancelled ? "⊘" : "⚠"}</span>
        <span>
          {DECISIONS[notice.decision] ?? "Unavailable:"} {label}
          {high > 0 && <span className="notice-high"> · {high} high impact</span>}
        </span>
      </summary>
      {cancelled && notice.question && <p className="muted small">Question: “{notice.question}”</p>}
      <ul>
        {items.map((i, n) => (
          <li key={n}>
            <strong>{i.role}</strong> · {i.tool}
            <span className={i.impact === "high" ? "notice-high" : "muted"}>
              {" "}· {i.impact === "high" ? `no ${(i.gaps ?? []).join(", ")}` : `covered by ${(i.covered_by ?? []).join(", ")}`}
            </span>
            {i.reason && <span className="muted"> ({i.reason})</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}
