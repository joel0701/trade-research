/** Interactive tables for markdown answers: sticky header, sorting, row highlight, filter, copy as CSV. */
import { ReactNode, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type HastNode = { type: string; tagName?: string; value?: string; properties?: Record<string, any>; children?: HastNode[] };
type Cell = { text: string; node: HastNode; align?: string };

const textOf = (n: HastNode): string => n.type === "text" ? (n.value ?? "") : (n.children ?? []).map(textOf).join("");

/** Render a cell's inline markdown (bold, links, code…) from the parsed tree. */
function inline(n: HastNode, key: number | string = 0): ReactNode {
  if (n.type === "text") return n.value;
  const kids = (n.children ?? []).map((c, i) => inline(c, i));
  switch (n.tagName) {
    case "strong": return <strong key={key}>{kids}</strong>;
    case "em": return <em key={key}>{kids}</em>;
    case "del": return <del key={key}>{kids}</del>;
    case "code": return <code key={key}>{kids}</code>;
    case "br": return <br key={key} />;
    case "a": return <a key={key} href={n.properties?.href} target="_blank" rel="noreferrer">{kids}</a>;
    default: return <span key={key}>{kids}</span>;
  }
}

const SUFFIX: Record<string, number> = { k: 1e3, m: 1e6, b: 1e9, t: 1e12 };

/** A sortable number from text like "$1,234.5", "-3.2%", "(12)", "4.1B", "+0.8x"; null if not numeric. */
export function toNumber(text: string): number | null {
  let t = text.trim().replace(/[$€£¥,\s]/g, "").replace(/^\+/, "");
  if (!t || t === "—" || t === "-" || t.toLowerCase() === "n/a") return null;
  let sign = 1;
  if (/^\(.*\)$/.test(t)) { sign = -1; t = t.slice(1, -1); }
  const m = t.match(/^(-?\d*\.?\d+(?:e[-+]?\d+)?)(%|x|[kmbt])?$/i);
  if (!m) return null;
  return sign * parseFloat(m[1]) * (m[2] && SUFFIX[m[2].toLowerCase()] ? SUFFIX[m[2].toLowerCase()] : 1);
}

function rowsOf(node: HastNode): { head: Cell[]; body: Cell[][] } {
  const rows: { cells: Cell[]; header: boolean }[] = [];
  const walk = (n: HastNode, inHead: boolean) => {
    if (n.tagName === "tr") {
      const cells = (n.children ?? []).filter((c) => c.tagName === "th" || c.tagName === "td")
        .map((c) => ({ text: textOf(c).trim(), node: c, align: c.properties?.align ?? c.properties?.style?.match?.(/text-align:\s*(\w+)/)?.[1] }));
      rows.push({ cells, header: inHead || cells.every((c) => c.node.tagName === "th") });
      return;
    }
    (n.children ?? []).forEach((c) => walk(c, inHead || n.tagName === "thead"));
  };
  walk(node, false);
  const headIdx = rows.findIndex((r) => r.header);
  const head = headIdx >= 0 ? rows[headIdx].cells : [];
  return { head, body: rows.filter((_, i) => i !== headIdx).map((r) => r.cells) };
}

const csvCell = (s: string) => /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;

export function DataTable({ node }: { node: HastNode }) {
  const { head, body } = useMemo(() => rowsOf(node), [node]);
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const [active, setActive] = useState<number | null>(null);  // index into the original rows
  const [filter, setFilter] = useState("");
  const [copied, setCopied] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  // A column is numeric when most of its non-empty cells parse as numbers.
  const numeric = useMemo(() => head.map((_, c) => {
    const vals = body.map((r) => r[c]?.text ?? "").filter((t) => t && t !== "—");
    return vals.length > 0 && vals.filter((t) => toNumber(t) !== null).length / vals.length >= 0.8;
  }), [head, body]);

  const order = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let idx = body.map((_, i) => i).filter((i) => !q || body[i].some((c) => c.text.toLowerCase().includes(q)));
    if (sort) {
      const { col, dir } = sort;
      idx = [...idx].sort((a, b) => {
        const ta = body[a][col]?.text ?? "", tb = body[b][col]?.text ?? "";
        if (numeric[col]) {
          const na = toNumber(ta), nb = toNumber(tb);
          if (na === null && nb === null) return 0;
          if (na === null) return 1;   // blanks last either way
          if (nb === null) return -1;
          return (na - nb) * dir;
        }
        if (!ta) return 1;
        if (!tb) return -1;
        return ta.localeCompare(tb, undefined, { numeric: true, sensitivity: "base" }) * dir;
      });
    }
    return idx;
  }, [body, sort, filter, numeric]);

  const clickHeader = (col: number) => setSort((s) =>
    !s || s.col !== col ? { col, dir: numeric[col] ? -1 : 1 } : s.dir === (numeric[col] ? -1 : 1) ? { col, dir: (s.dir * -1) as 1 | -1 } : null);

  function onKey(e: React.KeyboardEvent) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const pos = active === null ? -1 : order.indexOf(active);
    const next = Math.min(order.length - 1, Math.max(0, pos + (e.key === "ArrowDown" ? 1 : -1)));
    setActive(order[next]);
    wrap.current?.querySelectorAll("tbody tr")[next]?.scrollIntoView({ block: "nearest" });
  }

  async function copyCsv() {
    const lines = [head.map((c) => csvCell(c.text)).join(","), ...order.map((i) => body[i].map((c) => csvCell(c.text)).join(","))];
    try { await navigator.clipboard.writeText(lines.join("\n")); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* clipboard blocked */ }
  }

  const tone = (text: string, col: number) => {
    if (!numeric[col] || !/^[+-]|^\(/.test(text.trim())) return "";
    const n = toNumber(text);
    return n === null || n === 0 ? "" : n > 0 ? "pos" : "neg";
  };
  const big = body.length > 5;

  return (
    <div className="dt">
      {big && (
        <div className="dt-bar">
          <span className="muted small">{filter ? `${order.length} of ${body.length}` : body.length} rows
            {sort && <> · sorted by <strong>{head[sort.col]?.text}</strong> {sort.dir === 1 ? "↑" : "↓"}</>}</span>
          <input className="dt-filter" placeholder="Filter rows…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <button className="act" onClick={copyCsv} title="Copy the visible rows as CSV">{copied ? "Copied" : "Copy CSV"}</button>
        </div>
      )}
      <div className={`dt-wrap ${body.length > 14 ? "dt-tall" : ""}`} ref={wrap} tabIndex={0} onKeyDown={onKey}
           aria-label="Table: click a header to sort, arrow keys to move between rows">
        <table>
          {head.length > 0 && (
            <thead>
              <tr>
                {head.map((c, i) => {
                  const on = sort?.col === i;
                  return (
                    <th key={i} className={`${numeric[i] ? "num" : ""} ${on ? "sorted" : ""}`}
                        aria-sort={on ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}
                        onClick={() => clickHeader(i)} title="Sort">
                      <span>{inline(c.node)}</span><span className="dt-arrow">{on ? (sort!.dir === 1 ? "▲" : "▼") : "↕"}</span>
                    </th>
                  );
                })}
              </tr>
            </thead>
          )}
          <tbody>
            {order.map((i) => (
              <tr key={i} className={active === i ? "active" : ""} onClick={() => setActive(active === i ? null : i)}>
                {body[i].map((c, j) => (
                  <td key={j} className={`${numeric[j] ? "num" : ""} ${tone(c.text, j)}`}
                      style={c.align && !numeric[j] ? { textAlign: c.align as any } : undefined}>{inline(c.node)}</td>
                ))}
              </tr>
            ))}
            {order.length === 0 && <tr><td colSpan={head.length || 1} className="muted">No rows match “{filter}”.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Markdown with interactive tables (used for answers and the trace). */
export function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ table: ({ node }) => <DataTable node={node as any} /> }}>
      {text}
    </ReactMarkdown>
  );
}
