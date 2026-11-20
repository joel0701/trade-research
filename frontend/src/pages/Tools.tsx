import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Field, JsonField, MasterDetail, Toggle, useCrud } from "../admin";
import { PRICING, PricingIcon, PricingLegend } from "../Pricing";

type Tab = "sources" | "connections" | "models";
type Capability = { slug: string; label: string; hint: string };
type Status = { id: number; available: boolean; reason: string | null; pricing?: string };

const SOURCE_KIND_HELP: Record<string, string> = {
  python: 'Built-in Python function. Config: {"function": "<name>"}.',
  claude_server_tool: 'Anthropic-hosted tool (Claude models only), e.g. {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}.',
  mcp_server: 'Tools from the connection\'s MCP server. Config: {"allowed_tools": [...]} to pick which ones '
    + '(empty = all), {"blocked_tools": [...]} to exclude some. Tools that place, change or cancel orders are always blocked.',
};

const CONNECTION_KIND_HELP: Record<string, string> = {
  mcp: 'MCP server. HTTP: {"transport": "http", "url": "https://…/mcp", "headers": {}}. Local: {"transport": "stdio", '
    + '"command", "args", "env"}. The secret is sent as "Authorization: Bearer …" (change with "secret_header" / "secret_prefix").',
  http: "A web API used by built-in Python tools (e.g. SEC EDGAR). The named environment variable must be set.",
  none: "No setup needed.",
};

const MODEL_HELP = 'LLM. Config: {"provider": anthropic|openai|google|meta|xai|mistral|deepseek|ollama|lmstudio|mlx|local, "model_id", "max_tokens", '
  + '"price_input", "price_output", "price_cache_read" (USD per million tokens)}. Claude only: "effort", "fallbacks", '
  + '"show_thinking". Other vendors: "params" (extra request fields). The vendor\'s API key goes in .env.';

function Tabs({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const tabs: [Tab, string][] = [["sources", "Data sources"], ["connections", "Connections"], ["models", "Models"]];
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([t, label]) => (
        <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""}
                onClick={() => setTab(t)}>{label}</button>
      ))}
    </div>
  );
}

/** Models installed on local servers (Ollama, LM Studio, MLX, …), with one-click Add. */
function LocalModels({ onAdded }: { onAdded: () => void }) {
  const [servers, setServers] = useState<Json[] | null>(null);
  const [busy, setBusy] = useState("");
  const load = () => { setServers(null); api.get("/local-models").then(setServers).catch(() => setServers([])); };
  useEffect(load, []);
  async function add(provider: string, model_id: string) {
    setBusy(`${provider}:${model_id}`);
    try { await api.post("/local-models", { provider, model_id }); onAdded(); load(); } finally { setBusy(""); }
  }
  const running = (servers ?? []).filter((s) => s.running);
  return (
    <div className="local-models">
      <div className="inline" style={{ justifyContent: "space-between" }}>
        <strong>Local models</strong>
        <button className="link" onClick={load}>Refresh</button>
      </div>
      {servers === null ? <p className="muted small">Looking for local model servers…</p> : (
        <>
          <ul className="plain">
            {servers.map((s) => (
              <li key={s.provider}>
                <span className={`dot ${s.running ? "ok" : "off"}`} />
                <strong>{s.name}</strong>{" "}
                <span className="muted small">{s.base_url ? s.base_url : `set ${s.base_url_env} in .env`}
                  {s.running ? ` · ${s.models.length} model${s.models.length === 1 ? "" : "s"}` : " · not running"}</span>
                {s.running && s.models.length > 0 && (
                  <div className="local-list">
                    {s.models.map((m: Json) => (
                      <span key={m.model_id} className={`local-model ${m.loaded === false ? "unloaded" : ""}`}
                            title={m.loaded === true ? "Loaded and ready" : m.loaded === false ? `Not loaded in ${s.name.split(" (")[0]}` : ""}>
                        {m.loaded != null && <span className={`dot ${m.loaded ? "ok" : "off"}`} />}
                        <code>{m.model_id}</code>
                        {m.loaded === false && <span className="muted small">not loaded</span>}
                        {m.context_low && <span className="ctx-warn" title={`This app needs at least ${s.min_context.toLocaleString()} tokens of context`}>
                          context {m.context.toLocaleString()}: raise to {s.min_context.toLocaleString()}+</span>}
                        {m.tool_id ? <span className="local-added">✓ in your models</span> :
                          <button className="act act-accent" disabled={busy === `${s.provider}:${m.model_id}`}
                                  title="Add it to your models so it appears in the model picker"
                                  onClick={() => add(s.provider, m.model_id)}>+ Add to models</button>}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {running.some((s) => s.models.some((m: Json) => !m.tool_id)) && (
            <p className="muted small">Green dot: loaded and ready. Click <strong>+ Add to models</strong> to make a model
              selectable in Research, Team, Skills and Scanners. Models that aren't loaded need loading in their app first
              (Ollama loads them automatically).</p>
          )}
          {running.length === 0 && (
            <p className="muted small">No local server is running. Start Ollama (<code>ollama serve</code>, then
              <code> ollama pull qwen2.5:14b</code>), LM Studio's local server, or MLX
              (<code>mlx_lm.server --model mlx-community/Qwen2.5-14B-Instruct-4bit</code>), then Refresh.
              Local models are free; pick ones that support tool calling so they can consult the team.</p>
          )}
        </>
      )}
    </div>
  );
}

function StatusBadge({ s }: { s?: Status }) {
  if (!s) return null;
  return s.available
    ? <span className="dot ok" title="Ready" />
    : <span className="dot off" title={s.reason ?? "Unavailable"} />;
}

export default function Tools() {
  const [tab, setTab] = useState<Tab>(() => {
    try { return (localStorage.getItem("toolsTab") as Tab) || "sources"; } catch { return "sources"; }
  });
  const changeTab = (t: Tab) => { setTab(t); try { localStorage.setItem("toolsTab", t); } catch { /* ignore */ } };

  const tools = useCrud("/tools");
  const connections = useCrud("/connections");
  const [caps, setCaps] = useState<Capability[]>([]);
  const [status, setStatus] = useState<Record<number, Status>>({});
  const [models, setModels] = useState<Record<number, Json>>({});
  const [functions, setFunctions] = useState<{ name: string; description: string }[]>([]);

  const refreshStatus = () => {
    api.get<Status[]>("/tools-status").then((ss) => setStatus(Object.fromEntries(ss.map((s) => [s.id, s])))).catch(() => {});
    api.get("/models").then((ms) => setModels(Object.fromEntries(ms.map((m: Json) => [m.id, m])))).catch(() => {});
  };
  useEffect(() => {
    api.get<Capability[]>("/capabilities").then(setCaps);
    api.get("/python-functions").then(setFunctions);
  }, []);
  useEffect(refreshStatus, [tools.items, connections.items]);

  const connName = (id: number | null) => connections.items.find((c) => c.id === id)?.name ?? "—";
  const connPricing = (id: number | null) => connections.items.find((c) => c.id === id)?.pricing ?? "";
  const tabs = <Tabs tab={tab} setTab={changeTab} />;
  const header = tab === "models" ? tabs : <>{tabs}<PricingLegend /></>;

  if (tab === "connections") {
    const usedBy = (id: number) => tools.items.filter((t) => t.connection_id === id);
    return (
      <MasterDetail title="Connections" crud={connections} header={header}
        blank={{ slug: "", name: "", kind: "mcp", description: "", config: { transport: "http", url: "" }, secret_env: null, pricing: "", enabled: true }}
        rowIcon={(c) => <PricingIcon pricing={c.pricing} />}
        rowExtra={(c) => {
          const n = usedBy(c.id).length;
          return <span className="muted small nowrap">{n} {n === 1 ? "source" : "sources"}</span>;
        }}>
        {(c, set) => (
          <>
            <Field label="Name" value={c.name} onChange={(name) => set({ name })} />
            <Field label="Slug (unique id; prefixes this connection's MCP tool names)" value={c.slug} onChange={(slug) => set({ slug })} />
            <label>Kind</label>
            <select value={c.kind} onChange={(e) => set({ kind: e.target.value })}>
              {Object.keys(CONNECTION_KIND_HELP).map((k) => <option key={k}>{k}</option>)}
            </select>
            <p className="muted small">{CONNECTION_KIND_HELP[c.kind]}</p>
            <label>Cost</label>
            <select value={c.pricing ?? ""} onChange={(e) => set({ pricing: e.target.value })}>
              <option value="">Not set</option>
              {PRICING.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
            <p className="muted small">
              {PRICING.find((p) => p.value === c.pricing)?.hint ?? "Shown as an icon on this connection and its data sources."}
            </p>
            <Field label="Description" value={c.description} onChange={(description) => set({ description })} multiline rows={2} />
            {c.kind !== "none" && <JsonField label="Config" value={c.config} onChange={(config) => set({ config })} rows={6} />}
            <Field label="Environment variable that holds the key (set its value in .env, never here)"
                   value={c.secret_env ?? ""} onChange={(v) => set({ secret_env: v || null })} />
            <Toggle label="Enabled" value={c.enabled} onChange={(enabled) => set({ enabled })} />
            {c.id && (
              <>
                <label>Data sources using this connection</label>
                <ul className="plain">
                  {usedBy(c.id).map((t) => (
                    <li key={t.id}><StatusBadge s={status[t.id]} /> <PricingIcon pricing={c.pricing} />{t.name}
                      {status[t.id] && !status[t.id].available && <span className="muted small"> — {status[t.id].reason}</span>}</li>
                  ))}
                  {usedBy(c.id).length === 0 && <li className="muted">None yet. Add one under Data sources.</li>}
                </ul>
              </>
            )}
          </>
        )}
      </MasterDetail>
    );
  }

  if (tab === "models") {
    return (
      <MasterDetail title="Models" crud={tools} header={<>{header}<LocalModels onAdded={() => tools.reload()} /></>}
        filter={(t) => t.kind === "model"}
        blank={{ slug: "", name: "", kind: "model", description: "", config: { provider: "anthropic", model_id: "", max_tokens: 16000 }, enabled: true }}
        rowExtra={(t) => {
          const m = models[t.id];
          const ready = !!m?.available && m?.loaded !== false;
          const title = !m ? "" : m.local
            ? (!m.available ? `${m.provider_name} isn't running` : m.loaded === false ? `Not loaded in ${m.provider_name}` : "Loaded and ready")
            : m.available ? "Key found" : `No key: ${m.key_env ?? ""}`;
          return <span className={`dot ${ready ? "ok" : "off"}`} title={title} />;
        }}>
        {(t, set) => (
          <>
            <Field label="Name" value={t.name} onChange={(name) => set({ name })} />
            <Field label="Slug" value={t.slug} onChange={(slug) => set({ slug })} />
            <p className="muted small">{MODEL_HELP}</p>
            <Field label="Description" value={t.description} onChange={(description) => set({ description })} multiline rows={2} />
            <JsonField label="Config" value={t.config} onChange={(config) => set({ config })} />
            <Toggle label="Strict team mode when this model is the Lead" value={!!t.config?.strict_team}
                    onChange={(on) => set({ config: { ...t.config, strict_team: on } })} />
            <p className="muted small" style={{ marginTop: 2 }}>
              Off (default): up to 2 consults per specialist and 5 in total per question. On: 1 per specialist, 3 in
              total, and specialists keep their reports short. Faster, useful for slow local models, but may reduce
              quality.
            </p>
            {models[t.id] && (models[t.id].available
              ? (models[t.id].local && models[t.id].loaded === false
                  ? <p className="error small">Not loaded: load it in {models[t.id].provider_name} first (or turn on just-in-time loading there).</p>
                  : <p className="small" style={{ color: "var(--ok-fg)" }}>✓ {models[t.id].local
                      ? `Loaded in ${models[t.id].provider_name}${models[t.id].context ? ` · context ${models[t.id].context.toLocaleString()} tokens` : ""}`
                      : `${models[t.id].provider_name} API key found`}</p>)
              : models[t.id].local
                ? <p className="error small">{models[t.id].provider_name} isn't running{models[t.id].base_url ? ` at ${models[t.id].base_url}` : ""}. Start it, then reload.</p>
                : <p className="error small">No API key: set {models[t.id].key_env} in .env and restart the backend.</p>)}
            <Toggle label="Enabled" value={t.enabled} onChange={(enabled) => set({ enabled })} />
          </>
        )}
      </MasterDetail>
    );
  }

  const groups = caps.map((c) => ({
    key: c.slug, label: c.label,
    match: (t: Json) => (t.capabilities?.length ? t.capabilities.includes(c.slug) : c.slug === "other"),
  }));
  return (
    <MasterDetail title="Data sources" crud={tools} header={header} filter={(t) => t.kind !== "model"} groups={groups}
      blank={{ slug: "", name: "", kind: "mcp_server", description: "", connection_id: null, capabilities: [], config: {}, notes: "", enabled: true }}
      rowIcon={(t) => <PricingIcon pricing={connPricing(t.connection_id)} />}
      rowExtra={(t) => <StatusBadge s={status[t.id]} />}>
      {(t, set) => (
        <>
          <Field label="Name" value={t.name} onChange={(name) => set({ name })} />
          <Field label="Slug (unique id)" value={t.slug} onChange={(slug) => set({ slug })} />
          <label>Categories</label>
          <div className="checks">
            {caps.map((c) => (
              <label key={c.slug} title={c.hint}>
                <input type="checkbox" checked={(t.capabilities ?? []).includes(c.slug)}
                       onChange={(e) => set({ capabilities: e.target.checked
                         ? [...(t.capabilities ?? []), c.slug]
                         : (t.capabilities ?? []).filter((x: string) => x !== c.slug) })} />
                {c.label}
              </label>
            ))}
          </div>
          <label>Connection</label>
          <select value={t.connection_id ?? ""} onChange={(e) => set({ connection_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">None</option>
            {connections.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <label>Kind</label>
          <select value={t.kind} onChange={(e) => set({ kind: e.target.value })}>
            {Object.keys(SOURCE_KIND_HELP).map((k) => <option key={k}>{k}</option>)}
          </select>
          <p className="muted small">{SOURCE_KIND_HELP[t.kind]}</p>
          {t.kind === "python" && <p className="muted small">Built-in functions: {functions.map((f) => f.name).join(", ")}</p>}
          <Field label="Description (shown to the model; optional for built-ins)" value={t.description}
                 onChange={(description) => set({ description })} multiline rows={2} />
          <JsonField label="Config" value={t.config} onChange={(config) => set({ config })} />
          <Field label="Notes: what it supports and its limits (e.g. a fixed lookback). Shown to the agents, and added as a callout to skill and scanner prompts that use this source."
                 value={t.notes ?? ""} onChange={(notes) => set({ notes })} multiline rows={3} />
          {t.connection_id && (
            <p className="muted small"><PricingIcon pricing={connPricing(t.connection_id)} />
              {" "}{PRICING.find((p) => p.value === connPricing(t.connection_id))?.label ?? "Cost not set"} (set on connection {connName(t.connection_id)})</p>
          )}
          <Toggle label="Enabled" value={t.enabled} onChange={(enabled) => set({ enabled })} />
          {t.id && status[t.id] && (status[t.id].available
            ? <p className="small" style={{ color: "var(--ok-fg)" }}>✓ Ready{t.connection_id ? ` (via ${connName(t.connection_id)})` : ""}</p>
            : <p className="error small">Not available: {status[t.id].reason}</p>)}
        </>
      )}
    </MasterDetail>
  );
}
