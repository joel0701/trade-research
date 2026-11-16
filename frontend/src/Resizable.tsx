/** Draggable dividers for resizing the side panels. Widths are remembered per browser. */
import { useCallback, useRef, useState } from "react";

/** A width in pixels, clamped to [min, max] and saved in localStorage under `key`. */
export function usePanelWidth(key: string, initial: number, min: number, max: number) {
  const clamp = useCallback((w: number) => Math.round(Math.min(max, Math.max(min, w))), [min, max]);
  const [width, setWidthState] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(key));
      return saved ? clamp(saved) : initial;
    } catch { return initial; }
  });
  const setWidth = useCallback((w: number) => {
    const v = clamp(w);
    setWidthState(v);
    try { localStorage.setItem(key, String(v)); } catch { /* storage unavailable */ }
  }, [key, clamp]);
  return { width, setWidth, reset: () => setWidth(initial), min, max };
}

/**
 * A vertical divider. `side` says which panel it resizes: "left" grows when dragged right,
 * "right" grows when dragged left.
 */
export function ResizeHandle({ side, panel, label }: {
  side: "left" | "right";
  panel: ReturnType<typeof usePanelWidth>;
  label: string;
}) {
  const start = useRef<{ x: number; width: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const sign = side === "left" ? 1 : -1;

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = { x: e.clientX, width: panel.width };
    setDragging(true);
    document.body.classList.add("resizing");
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!start.current) return;
    panel.setWidth(start.current.width + sign * (e.clientX - start.current.x));
  }

  function end(e: React.PointerEvent<HTMLDivElement>) {
    if (!start.current) return;
    start.current = null;
    setDragging(false);
    document.body.classList.remove("resizing");
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const step = e.shiftKey ? 64 : 16;
    if (e.key === "ArrowLeft") { e.preventDefault(); panel.setWidth(panel.width - sign * step); }
    if (e.key === "ArrowRight") { e.preventDefault(); panel.setWidth(panel.width + sign * step); }
    if (e.key === "Home") { e.preventDefault(); panel.reset(); }
  }

  return (
    <div className={`resize-handle ${side} ${dragging ? "dragging" : ""}`}
         role="separator" aria-orientation="vertical" aria-label={label}
         aria-valuenow={panel.width} aria-valuemin={panel.min} aria-valuemax={panel.max}
         tabIndex={0} title={`${label}: drag to resize, double-click to reset`}
         onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={end} onPointerCancel={end}
         onDoubleClick={panel.reset} onKeyDown={onKeyDown} />
  );
}
