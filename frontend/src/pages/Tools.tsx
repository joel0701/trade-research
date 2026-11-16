import { useEffect, useState } from "react";
import { api } from "../api";
import { Field, JsonField, MasterDetail, Toggle, useCrud } from "../admin";

const KIND_HELP: Record<string, string> = {
  model: 'Claude model. Config: {"model_id", "effort" (low…max), "max_tokens", "fallbacks": "default", "show_thinking"}',
  python: 'Built-in Python function. Config: {"function": "<name>"} — see the list below.',
  claude_server_tool: 'Anthropic-hosted tool, e.g. {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}',
  mcp_server: 'MCP server. HTTP: {"transport": "http", "url", "headers", "allowed_tools", "blocked_tools"}. '
    + 'Local: {"transport": "stdio", "command", "args", "env"}. The secret env var is sent as a Bearer token (http) or passed in env (stdio).',
};

export default function Tools() {
  const crud = useCrud("/tools");
  const [functions, setFunctions] = useState<{ name: string; description: string }[]>([]);
  useEffect(() => { api.get("/python-functions").then(setFunctions); }, []);
  return (
    <MasterDetail title="Tools" crud={crud}
      blank={{ slug: "", name: "", kind: "python", description: "", config: {}, secret_env: null, enabled: true }}>
      {(t, set) => (
        <>
          <Field label="Name" value={t.name} onChange={(name) => set({ name })} />
          <Field label="Slug (unique id, used in tool names)" value={t.slug} onChange={(slug) => set({ slug })} />
          <label>Kind</label>
          <select value={t.kind} onChange={(e) => set({ kind: e.target.value })}>
            {Object.keys(KIND_HELP).map((k) => <option key={k}>{k}</option>)}
          </select>
          <p className="muted">{KIND_HELP[t.kind]}</p>
          <Field label="Description (shown to the model)" value={t.description} onChange={(description) => set({ description })} multiline rows={2} />
          <JsonField label="Config" value={t.config} onChange={(config) => set({ config })} />
          <Field label="Secret environment variable name (set its value in .env, never here)"
                 value={t.secret_env ?? ""} onChange={(v) => set({ secret_env: v || null })} />
          <Toggle label="Enabled" value={t.enabled} onChange={(enabled) => set({ enabled })} />
          {t.kind === "python" && (
            <p className="muted">Available functions: {functions.map((f) => f.name).join(", ")}</p>
          )}
        </>
      )}
    </MasterDetail>
  );
}
