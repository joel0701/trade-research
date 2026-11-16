import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Avatar, browserZone, useSettings } from "../settings";

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
