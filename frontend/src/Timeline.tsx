/** Timeline rail: one marker per message (rings for your questions, dots for answers).
 *  Hover a marker for its time and first line; click to jump; the message in view is highlighted. */
import { RefObject, useEffect, useState } from "react";
import { parseTime } from "./Trace";

export type TimelineItem = { key: string; role: "user" | "assistant"; text: string; created_at?: string; label?: string };

function when(iso?: string) {
  const d = iso ? new Date(parseTime(iso)) : new Date();
  return d.toLocaleString(undefined, { month: "2-digit", day: "2-digit", year: "numeric",
                                       hour: "2-digit", minute: "2-digit", hour12: false });
}

function firstLine(text: string) {
  const line = text.replace(/[#*_`>|]/g, "").split("\n").map((l) => l.trim()).find(Boolean) ?? "";
  return line.length > 90 ? line.slice(0, 90) + "…" : line;
}

const HINT_KEY = "timelineHintSeen";

export default function Timeline({ items, scroller }: { items: TimelineItem[]; scroller: RefObject<HTMLDivElement> }) {
  const [active, setActive] = useState(0);
  // The tooltip is drawn outside the scrolling rail (so it isn't clipped), at the marker's position.
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const show = (i: number, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    setHover({ i, x: r.right + 12, y: r.top + r.height / 2 });
  };
  const [hint, setHint] = useState(() => {
    try { return localStorage.getItem(HINT_KEY) !== "1"; } catch { return false; }
  });

  // Track which message is at the top of the view.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const update = () => {
      const top = el.getBoundingClientRect().top + 80;
      const nodes = Array.from(el.querySelectorAll<HTMLElement>("[data-msg-index]"));
      let current = 0;
      for (const n of nodes) {
        if (n.getBoundingClientRect().top <= top) current = Number(n.dataset.msgIndex);
      }
      // At the very bottom, the last message is the one being read.
      if (el.scrollTop + el.clientHeight >= el.scrollHeight - 4 && nodes.length) current = nodes.length - 1;
      setActive(current);
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    return () => el.removeEventListener("scroll", update);
  }, [scroller, items.length]);

  function jump(i: number) {
    scroller.current?.querySelector<HTMLElement>(`[data-msg-index="${i}"]`)
      ?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function dismissHint() {
    setHint(false);
    try { localStorage.setItem(HINT_KEY, "1"); } catch { /* storage unavailable */ }
  }

  if (items.length < 2) return null;

  return (
    <nav className="timeline" aria-label="Conversation timeline">
      <div className="timeline-rail">
        {items.map((it, i) => (
          <button key={it.key} type="button"
                  className={`tl-marker ${it.role} ${i === active ? "active" : ""}`}
                  aria-label={`${it.role === "user" ? "Your question" : "Answer"}, ${when(it.created_at)}`}
                  onMouseEnter={(e) => show(i, e.currentTarget)} onMouseLeave={() => setHover(null)}
                  onFocus={(e) => show(i, e.currentTarget)} onBlur={() => setHover(null)}
                  onClick={() => jump(i)} />
        ))}
      </div>
      {hover && items[hover.i] && (
        <div className="tl-tip" role="tooltip" style={{ left: hover.x, top: hover.y }}>
          <span className="tl-tip-head">
            <strong>{items[hover.i].role === "user" ? "You" : items[hover.i].label || "Answer"}</strong>
            <span>{when(items[hover.i].created_at)}</span>
          </span>
          <span className="tl-tip-text">{firstLine(items[hover.i].text) || "(attachment)"}</span>
        </div>
      )}
      {hint && (
        <div className="tl-hint">
          <div className="tl-hint-title">TIMELINE</div>
          <div>Hover the rail to see every turn. Click any marker to jump.</div>
          <button type="button" className="link" onClick={dismissHint}>Got it</button>
        </div>
      )}
    </nav>
  );
}
