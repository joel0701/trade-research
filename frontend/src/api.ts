export type Json = Record<string, any>;

export const BACKEND_DOWN =
  "Can't reach the backend. Check that it is running (uv run uvicorn app.main:app --reload --port 8321 in the backend folder) "
  + "and look at that terminal for errors.";

/** Send an error to the backend's data/logs/frontend.log. Never throws; never logs its own failures. */
export function reportError(message: string, detail = "", level: "error" | "warning" = "error") {
  try {
    void fetch("/api/client-log", {
      method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
      body: JSON.stringify({ level, message: String(message).slice(0, 2000), detail: String(detail).slice(0, 6000),
                             url: location.href }),
    }).catch(() => {});
  } catch { /* logging must never break the app */ }
}

async function req<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(BACKEND_DOWN);
  }
  // The dev server answers 500/502/504 itself when it cannot forward to the backend.
  if ([500, 502, 504].includes(r.status) && !(r.headers.get("content-type") ?? "").includes("json")) {
    throw new Error(BACKEND_DOWN);
  }
  const isJson = (r.headers.get("content-type") ?? "").includes("json");
  if (r.status === 404 && !isJson) {
    throw new Error("Something other than the Trade Research backend answered on the backend port. "
      + "Check the backend terminal for errors such as 'address already in use'.");
  }
  if (!r.ok) {
    const text = await r.text();
    reportError(`${method} /api${path} failed: ${r.status}`, text, r.status < 500 ? "warning" : "error");
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json();
}

export const api = {
  get: <T = any>(p: string) => req<T>("GET", p),
  post: <T = any>(p: string, b?: unknown) => req<T>("POST", p, b ?? {}),
  put: <T = any>(p: string, b: unknown) => req<T>("PUT", p, b),
  patch: <T = any>(p: string, b: unknown) => req<T>("PATCH", p, b),
  del: <T = any>(p: string) => req<T>("DELETE", p),
};

export type StreamEvent =
  | { type: "text" | "thinking"; source: string; text: string }
  | { type: "tool_call"; source: string; name: string; input?: Json }
  | { type: "specialist_start"; role: string; name: string; question: string }
  | { type: "specialist_end"; role: string; name: string; summary: string }
  | { type: "error"; message: string }
  | ({ type: "confirm_needed" } & UnavailableReport)
  | { type: "done"; usage: Json }
  | { type: "stage"; label: string };  // the next stage starts (e.g. a skill evaluating a scanner's analysis)

/** POST a message and read the Server-Sent Events stream. */
export type UnavailableItem = {
  role: string; role_slug: string; tool: string; tool_id: number; reason: string | null;
  categories: string[]; impact: "high" | "low"; gaps: string[]; covered_by: string[];
};
export type UnavailableReport = { fingerprint: string; summary: string; items: UnavailableItem[] };

export type AttachmentInfo = { id: number; filename: string; kind: "pdf" | "image" | "text"; size: number };

/** Upload one file into a session; send its id with the next message. */
export async function uploadAttachment(sessionId: number, file: File): Promise<AttachmentInfo> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`/api/sessions/${sessionId}/attachments`, { method: "POST", body: form });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

export async function sendMessage(sessionId: number, text: string, onEvent: (e: StreamEvent) => void,
                                  attachmentIds: number[] = [],
                                  confirm: { proceed?: string; remember?: boolean; hidden?: boolean } = {}) {
  const handle = onEvent;
  onEvent = (e: StreamEvent) => {
    if (e.type === "error") reportError(`Session ${sessionId}: ${e.message}`, "", "error");
    handle(e);
  };
  const r = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, attachment_ids: attachmentIds, ...confirm }),
  });
  if (!r.ok || !r.body) {
    const text = await r.text();
    reportError(`Sending a message to session ${sessionId} failed: ${r.status}`, text);
    throw new Error(`${r.status}: ${text}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      if (chunk.startsWith("data: ")) onEvent(JSON.parse(chunk.slice(6)));
    }
  }
}
