import React from "react";
import { reportError } from "./api";

// Uncaught browser errors go to data/logs/frontend.log too.
window.addEventListener("error", (e) => reportError(e.message || "Uncaught error",
  `${e.filename ?? ""}:${e.lineno ?? ""}:${e.colno ?? ""}\n${e.error?.stack ?? ""}`));
window.addEventListener("unhandledrejection", (e) => reportError(`Unhandled promise rejection: ${e.reason?.message ?? e.reason}`,
  e.reason?.stack ?? ""));
import { Chevron, Flask, Gear, Help, Moon, Sun, Users, Wrench } from "./icons";
import Logo from "./Logo";
import { usePopover } from "./Composer";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Research from "./pages/Research";
import Skills from "./pages/Skills";
import Scanners from "./pages/Scanners";
import Team from "./pages/Team";
import Tools from "./pages/Tools";
import Settings from "./pages/Settings";
import { Avatar, SettingsProvider, testMode, useSettings } from "./settings";
import { ConfirmProvider } from "./confirm";
import "./styles.css";

function lastSessionPath(): string {
  try {
    const id = localStorage.getItem("lastSession");
    return id ? `/research/${id}` : "/research";
  } catch { return "/research"; }
}

/** Profile icon menu: settings, plus the workspace screens (Team, Tools) and the theme. */
function ProfileMenu() {
  const { theme, toggleTheme, profile, save } = useSettings();
  const pop = usePopover();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const inMenu = ["/team", "/tools", "/settings"].some((p) => pathname.startsWith(p));
  const go = (to: string) => { pop.setOpen(false); navigate(to); };
  const item = (to: string, icon: React.ReactNode, label: string, hint: string) => (
    <button role="menuitem" className={`menu-item ${pathname.startsWith(to) ? "active" : ""}`} onClick={() => go(to)}>
      <span className="menu-icon">{icon}</span><span><strong>{label}</strong><small>{hint}</small></span>
    </button>
  );
  return (
    <div className="pop-anchor" ref={pop.ref}>
      <button className={`profile-btn ${inMenu ? "on" : ""}`} onClick={() => pop.setOpen(!pop.open)} aria-haspopup="menu"
              aria-expanded={pop.open} title="Profile, settings, team and tools">
        <Avatar size={28} /><Chevron />
      </button>
      {pop.open && (
        <div className="profile-menu" role="menu">
          <div className="menu-head">
            <Avatar size={36} />
            <div><strong>{profile.name || "Your profile"}</strong><small>{profile.email || "Name, photo, time zone"}</small></div>
          </div>
          {item("/settings", <Gear />, "Profile & settings", "Name, time zone, clock, theme")}
          <div className="menu-sep">Workspace</div>
          {item("/team", <Users />, "Team", "The Lead Analyst and specialists")}
          {item("/tools", <Wrench />, "Tools", "Data sources, connections, models")}
          <div className="menu-sep" />
          <button role="menuitem" className="menu-item" onClick={toggleTheme}>
            <span className="menu-icon">{theme === "dark" ? <Sun /> : <Moon />}</span>
            <span><strong>{theme === "dark" ? "Light mode" : "Dark mode"}</strong></span>
          </button>
          <button role="menuitemcheckbox" aria-checked={testMode(profile)} className="menu-item"
                  disabled={!!profile.mock_llm_env}
                  title={profile.mock_llm_env ? "On because MOCK_LLM=1 is set in .env" : "Canned answers instead of AI models; nothing is charged"}
                  onClick={() => save({ mock_llm: !profile.mock_llm }).catch(() => {})}>
            <span className="menu-icon"><Flask /></span>
            <span><strong>Test mode</strong><small>{profile.mock_llm_env ? "On (set in .env)" : "Canned answers, no AI cost"}</small></span>
            <span className={`switch ${testMode(profile) ? "on" : ""}`} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}

function App() {
  const navigate = useNavigate();
  const { profile } = useSettings();
  const mock = testMode(profile);
  const links = [["/research", "Research"], ["/skills", "Skills"], ["/scanners", "Scanners"]];
  return (
    <div className="app">
      <header className="appbar">
        <div className="appbar-top">
          <a className="brand" href="/research" onClick={(e) => { e.preventDefault(); navigate(lastSessionPath()); }}>
            <Logo size={30} /><span>Trade <b>Research</b></span>
          </a>
          {mock && (
            <span className="mock-badge" title="Test mode: answers are canned and free; data tools still run. Turn it off in the profile menu.">
              TEST MODE
            </span>
          )}
          <a className="support-link" href="/support.html" target="_blank" rel="noreferrer"
             title="Documentation: how the app works, its settings and configuration">
            <Help /><span>Support</span>
          </a>
          <ProfileMenu />
        </div>
        <nav className="topnav">
          {links.map(([to, label]) => (
            <NavLink key={to} to={to} onClick={(e) => {
              if (to !== "/research") return;
              e.preventDefault(); // go back to the session you were in, not a blank one
              navigate(lastSessionPath());
            }}>{label}</NavLink>
          ))}
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/research" />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:id" element={<Research />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/scanners" element={<Scanners />} />
          <Route path="/screeners" element={<Navigate to="/scanners" replace />} />
          <Route path="/team" element={<Team />} />
          <Route path="/roles" element={<Navigate to="/team" replace />} />
          <Route path="/tools" element={<Tools />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><BrowserRouter><SettingsProvider><ConfirmProvider><App /></ConfirmProvider></SettingsProvider></BrowserRouter></React.StrictMode>,
);
