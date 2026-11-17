/** Research session list with a right-click menu (Rename, Delete) and inline renaming. */
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useConfirm } from "./confirm";

export type SessionRow = { id: number; title: string; origin: string; updated_at: string; running?: boolean };

type Menu = { x: number; y: number; session: SessionRow };

export default function SessionList({ sessions, activeId, onOpen, onChanged, onDeleted, onError }: {
  sessions: SessionRow[];
  activeId: number | null;
  onOpen: (id: number) => void;
  onChanged: () => void;
  onDeleted: (id: number) => void;
  onError: (message: string) => void;
}) {
  const confirm = useConfirm();
  const [menu, setMenu] = useState<Menu | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  // Close the menu on any outside click, Escape, scroll or resize.
  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => {
      if (e instanceof MouseEvent && menuRef.current?.contains(e.target as Node)) return;
      setMenu(null);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenu(null);
    window.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  function openMenu(e: React.MouseEvent, session: SessionRow) {
    e.preventDefault();
    e.stopPropagation();
    // Keep the menu on screen near the right and bottom edges.
    const x = Math.min(e.clientX, window.innerWidth - 170);
    const y = Math.min(e.clientY, window.innerHeight - 100);
    setMenu({ x, y, session });
  }

  function startRename(s: SessionRow) {
    setMenu(null);
    setRenamingId(s.id);
    setDraft(s.title);
  }

  async function commitRename(s: SessionRow) {
    const title = draft.trim();
    setRenamingId(null);
    if (!title || title === s.title) return;
    try {
      await api.patch(`/sessions/${s.id}`, { title });
      onChanged();
    } catch (e: any) { onError(e.message); }
  }

  async function remove(s: SessionRow) {
    setMenu(null);
    const ok = await confirm({ title: "Delete this session?", confirmLabel: "Delete", danger: true,
                               message: `"${s.title}" and its trace will be permanently removed.` });
    if (!ok) return;
    try {
      await api.del(`/sessions/${s.id}`);
      onDeleted(s.id);
    } catch (e: any) { onError(e.message); }
  }

  return (
    <>
      {sessions.map((s) => (
        <div key={s.id}
             className={`item ${s.id === activeId ? "active" : ""}`}
             onClick={() => renamingId !== s.id && onOpen(s.id)}
             onContextMenu={(e) => openMenu(e, s)}>
          {renamingId === s.id ? (
            <input className="rename" autoFocus value={draft}
                   onFocus={(e) => e.target.select()}
                   onClick={(e) => e.stopPropagation()}
                   onChange={(e) => setDraft(e.target.value)}
                   onBlur={() => commitRename(s)}
                   onKeyDown={(e) => {
                     if (e.key === "Enter") commitRename(s);
                     if (e.key === "Escape") setRenamingId(null);
                   }} />
          ) : (
            <>
              <span title={s.title}>{s.running && <span className="spinner small" title="Researching…" />}{s.title}</span>
              <button className="link more" title="More actions" aria-label="More actions"
                      onClick={(e) => openMenu(e, s)}>⋯</button>
            </>
          )}
        </div>
      ))}
      {menu && (
        <div ref={menuRef} className="context-menu" role="menu" style={{ left: menu.x, top: menu.y }}>
          <button role="menuitem" onClick={() => startRename(menu.session)}>Rename</button>
          <button role="menuitem" className="danger" onClick={() => remove(menu.session)}>Delete</button>
        </div>
      )}
    </>
  );
}
