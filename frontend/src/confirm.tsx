/** In-app confirmation dialog (instead of the browser's "localhost says…" box).
 *  Usage: const confirm = useConfirm(); if (await confirm({ title, message })) { … } */
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";

type Options = { title: string; message?: string; confirmLabel?: string; danger?: boolean };
type Pending = Options & { resolve: (ok: boolean) => void };

const ConfirmContext = createContext<(o: Options) => Promise<boolean>>(async () => false);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const okRef = useRef<HTMLButtonElement>(null);

  const confirm = useCallback((o: Options) => new Promise<boolean>((resolve) => setPending({ ...o, resolve })), []);
  const close = (ok: boolean) => { pending?.resolve(ok); setPending(null); };

  useEffect(() => {
    if (!pending) return;
    okRef.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") close(false); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [pending]);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close(false)}>
          <div className="modal confirm-modal" role="alertdialog" aria-modal="true" aria-label={pending.title}>
            <div className="modal-head"><strong>{pending.title}</strong></div>
            {pending.message && <p className="muted">{pending.message}</p>}
            <div className="actions confirm-actions">
              <button onClick={() => close(false)}>Cancel</button>
              <button ref={okRef} className={pending.danger ? "danger-btn" : "primary"} onClick={() => close(true)}>
                {pending.confirmLabel ?? "OK"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export const useConfirm = () => useContext(ConfirmContext);
