import { useEffect, useState } from "react";
import { api } from "../api";
import { Field, JsonField, MasterDetail, Toggle, useCrud } from "../admin";

const KIND_HELP: Record<string, string> = {
  model: 'LLM. Config: {"provider": anthropic|openai|google|meta|xai|mistral|deepseek, "model_id", "max_tokens", '
    + '"price_input", "price_output", "price_cache_read" (USD per million tokens)}. Claude only: "effort", "fallbacks", '
    + '"show_thinking". Other vendors: "params" — extra request fields, e.g. {"reasoning_effort": "high"}. '
    + "The vendor's API key goes in .env.",
  python: 'Built-in Python function. Config: {"function": "<name>"} — see the list below.',
  claude_server_tool: 'Anthropic-hosted tool, e.g. {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}',
  mcp_server: 'MCP server. HTTP: {"transport": "http", "url", "headers", "allowed_tools", "blocked_tools"}. '
    + 'Local: {"transport": "stdio", "command", "args", "env"}. The secret env var is sent as a Bearer token (http) or passed in env (stdio).',
};

export default function Tools() {
  const crud = useCrud("/tools");
  const [functions, setFunctions] = useState<{ name: string; description: string }[]>([]);
  const [keys, setKeys] = useState<Record<number, { available: boolean; key_env: string; provider_name: string }>>({});
  useEffect(() => {
    api.get("/python-functions").then(setFunctions);
    api.get("/models").then((ms) => setKeys(Object.fromEntries(ms.map((m: any) => [m.id, m]))));
  }, []);
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
          {t.kind === "model" && keys[t.id] && (
            keys[t.id].available
              ? <p className="small" style={{ color: "var(--ok-fg)" }}>✓ {keys[t.id].provider_name} API key found</p>
              : <p className="error small">No API key: set {keys[t.id].key_env} in .env and restart the backend.</p>
          )}
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
