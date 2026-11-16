export type Json = Record<string, any>;

export const BACKEND_DOWN =
  "Can't reach the backend. Check that it is running (uv run uvicorn app.main:app --reload in the backend folder) "
  + "and look at that terminal for errors.";

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
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
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
  | { type: "done"; usage: Json };

/** POST a message and read the Server-Sent Events stream. */
export async function sendMessage(sessionId: number, text: string, onEvent: (e: StreamEvent) => void) {
  const r = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!r.ok || !r.body) throw new Error(`${r.status}: ${await r.text()}`);
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
