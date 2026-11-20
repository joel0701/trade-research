/** App-wide user profile and display settings (time zone, clock, theme). */
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { parseTime } from "./Trace";

export type Profile = {
  name: string; email: string; avatar_url: string | null;
  timezone: string; // IANA zone, or "auto" for the browser's zone
  hour12: boolean;
  mock_llm: boolean;      // Test mode: canned answers, no AI cost
  mock_llm_env?: boolean; // MOCK_LLM=1 in .env forces Test mode on
  model_low_id?: number | null;  // Default models: the real model each default points at
  model_mid_id?: number | null;
  model_high_id?: number | null;
};

const EMPTY: Profile = { name: "", email: "", avatar_url: null, timezone: "auto", hour12: false, mock_llm: false };

/** Test mode is on: the profile toggle, or MOCK_LLM=1 in .env. */
export const testMode = (p: Profile) => !!(p.mock_llm || p.mock_llm_env);

type Ctx = {
  profile: Profile;
  save: (patch: Partial<Profile>) => Promise<void>;
  uploadAvatar: (file: File) => Promise<void>;
  removeAvatar: () => Promise<void>;
  /** Format a stored (UTC) timestamp in the user's zone and clock style. */
  fmt: (iso?: string, opts?: { dateOnly?: boolean; timeOnly?: boolean }) => string;
  zone: string; // the effective IANA zone
  theme: string;
  toggleTheme: () => void;
};

const SettingsContext = createContext<Ctx | null>(null);

export const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<Profile>(EMPTY);
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("theme") ?? "dark"; } catch { return "dark"; }
  });

  useEffect(() => { api.get<Profile>("/profile").then(setProfile).catch(() => { /* keep defaults */ }); }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch { /* storage unavailable */ }
  }, [theme]);

  const save = useCallback(async (patch: Partial<Profile>) => {
    setProfile(await api.put<Profile>("/profile", patch));
  }, []);

  const uploadAvatar = useCallback(async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch("/api/profile/avatar", { method: "POST", body: form });
    if (!r.ok) {
      let msg = String(r.status);
      try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    setProfile(await r.json());
  }, []);

  const removeAvatar = useCallback(async () => { setProfile(await api.del<Profile>("/profile/avatar")); }, []);

  const zone = profile.timezone === "auto" ? browserZone() : profile.timezone;

  const fmt = useCallback((iso?: string, opts: { dateOnly?: boolean; timeOnly?: boolean } = {}) => {
    const d = iso ? new Date(parseTime(iso)) : new Date();
    const date: Intl.DateTimeFormatOptions = { month: "2-digit", day: "2-digit", year: "numeric" };
    const time: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", hour12: profile.hour12 };
    try {
      return d.toLocaleString(undefined, {
        timeZone: zone, ...(opts.timeOnly ? {} : date), ...(opts.dateOnly ? {} : time) });
    } catch {
      return d.toLocaleString(); // unknown zone in this browser: fall back to local time
    }
  }, [zone, profile.hour12]);

  const value = useMemo(() => ({
    profile, save, uploadAvatar, removeAvatar, fmt, zone, theme,
    toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  }), [profile, save, uploadAvatar, removeAvatar, fmt, zone, theme]);

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): Ctx {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used inside SettingsProvider");
  return ctx;
}

/** Round avatar: the uploaded photo, or initials. */
export function Avatar({ size = 36 }: { size?: number }) {
  const { profile } = useSettings();
  const initials = (profile.name || profile.email || "?")
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
  return profile.avatar_url
    ? <img className="avatar" src={profile.avatar_url} alt="" width={size} height={size} style={{ width: size, height: size }} />
    : <span className="avatar initials" style={{ width: size, height: size, fontSize: size * 0.38 }}>{initials}</span>;
}
