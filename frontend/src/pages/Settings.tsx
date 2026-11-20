import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Avatar, browserZone, testMode, useSettings } from "../settings";

type Provider = { slug: string; name: string; key_env: string; configured: boolean };

function zones(): string[] {
  try { return (Intl as any).supportedValuesOf("timeZone") as string[]; }
  catch { return ["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
                  "Europe/London", "Europe/Paris", "Asia/Tokyo", "Asia/Hong_Kong", "Australia/Sydney"]; }
}

export default function Settings() {
  const { profile, save, uploadAvatar, removeAvatar, fmt, zone, theme, toggleTheme } = useSettings();
  const [name, setName] = useState(profile.name);
  const [email, setEmail] = useState(profile.email);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [providers, setProviders] = useState<Provider[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const allZones = useMemo(zones, []);

  useEffect(() => { setName(profile.name); setEmail(profile.email); }, [profile.name, profile.email]);
  useEffect(() => { api.get<Provider[]>("/providers").then(setProviders).catch(() => {}); }, []);
  const [models, setModels] = useState<any[]>([]);
  useEffect(() => { api.get<any[]>("/models").then(setModels).catch(() => {}); }, [profile.model_low_id, profile.model_mid_id, profile.model_high_id]);

  async function run(action: () => Promise<void>, done = "Saved") {
    setError(""); setStatus("");
    try { await action(); setStatus(done); setTimeout(() => setStatus(""), 2000); }
    catch (e: any) { setError(e.message); }
  }

  const dirty = name !== profile.name || email !== profile.email;

  return (
    <div className="page settings">
      <h2>Settings</h2>

      <section className="card">
        <h3>Profile</h3>
        <div className="profile-row">
          <Avatar size={64} />
          <div className="inline">
            <button onClick={() => fileInput.current?.click()}>Upload photo</button>
            {profile.avatar_url && <button onClick={() => run(removeAvatar, "Photo removed")}>Remove</button>}
            <input ref={fileInput} type="file" accept=".png,.jpg,.jpeg,.webp,.gif" hidden
                   onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) run(() => uploadAvatar(f), "Photo updated"); }} />
          </div>
        </div>
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
        <label>Email</label>
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" type="email" />
        <div className="actions">
          <button className="primary" disabled={!dirty} onClick={() => run(() => save({ name, email }))}>Save profile</button>
        </div>
      </section>

      <section className="card">
        <h3>Date and time</h3>
        <p className="muted small">Times are stored in UTC and shown in this time zone.</p>
        <label>Time zone</label>
        <select value={profile.timezone} onChange={(e) => run(() => save({ timezone: e.target.value }))}>
          <option value="auto">Automatic (this computer: {browserZone()})</option>
          {allZones.map((z) => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
        </select>
        <label>Clock</label>
        <div className="inline">
          <label className="inline"><input type="radio" checked={!profile.hour12} onChange={() => run(() => save({ hour12: false }))} /> 24-hour</label>
          <label className="inline"><input type="radio" checked={profile.hour12} onChange={() => run(() => save({ hour12: true }))} /> 12-hour</label>
        </div>
        <p className="muted small">Now: {fmt()} ({zone.replace(/_/g, " ")})</p>
      </section>

      <section className="card">
        <h3>Default models</h3>
        <p className="muted small">Skills, team members and sessions can use a default instead of a specific model. Change a
          default here and everything set to it follows. Pick cheaper models for simple work and stronger ones for hard reasoning.</p>
        {([["low", "model_low_id", "Low-level", "simple lookups and summaries"],
           ["mid", "model_mid_id", "Mid-level", "the everyday workhorse"],
           ["high", "model_high_id", "High-level", "hard reasoning and final answers"]] as const).map(([tier, field, label, hint]) => {
          const current = models.find((m) => m.tier === tier);
          return (
            <div key={tier}>
              <label>{label} <span className="muted small">· {hint}</span></label>
              <select value={profile[field] ?? current?.target_id ?? ""}
                      onChange={(e) => run(() => save({ [field]: e.target.value ? Number(e.target.value) : null }))}>
                {models.filter((m) => !m.tier).map((m) => (
                  <option key={m.id} value={m.id}>{m.provider_name}: {m.name}{m.available ? "" : " (not available)"}</option>
                ))}
              </select>
            </div>
          );
        })}
      </section>

      <section className="card">
        <h3>Test mode</h3>
        <p className="muted small">Every team member gives canned answers instead of calling an AI model, so nothing is
          charged. Data tools still run, and answers still appear in the chat, trace and timeline. Use it to try the app
          or test a change for free.</p>
        <label className="inline">
          <input type="checkbox" checked={testMode(profile)} disabled={!!profile.mock_llm_env}
                 onChange={(e) => run(() => save({ mock_llm: e.target.checked }))} /> Test mode (canned answers, no AI cost)
        </label>
        {profile.mock_llm_env && <p className="muted small">On because MOCK_LLM=1 is set in .env. Remove it there and
          restart the backend to control Test mode here.</p>}
      </section>

      <section className="card">
        <h3>Appearance</h3>
        <div className="inline">
          <label className="inline"><input type="radio" checked={theme === "dark"} onChange={() => theme !== "dark" && toggleTheme()} /> Dark</label>
          <label className="inline"><input type="radio" checked={theme === "light"} onChange={() => theme !== "light" && toggleTheme()} /> Light</label>
        </div>
      </section>

      <section className="card">
        <h3>LLM vendors</h3>
        <p className="muted small">API keys live in the <code>.env</code> file, never in the app. Add a key there and restart the backend to enable a vendor's models.</p>
        <table className="vendors">
          <tbody>
            {providers.map((p) => (
              <tr key={p.slug}>
                <td>{p.name}</td>
                <td><code>{p.key_env}</code></td>
                <td>{p.configured ? <span className="status ok">key set</span> : <span className="muted">not set</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {(status || error) && <p className={error ? "error" : "muted"}>{error || status}</p>}
    </div>
  );
}
