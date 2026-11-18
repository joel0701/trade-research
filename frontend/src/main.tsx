import React, { useEffect, useState } from "react";
import { api } from "./api";
import { Chevron, Gear, Moon, Sun, Users, Wrench } from "./icons";
import Logo from "./Logo";
import { usePopover } from "./Composer";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Research from "./pages/Research";
import Skills from "./pages/Skills";
import Scanners from "./pages/Scanners";
import Roles from "./pages/Roles";
import Tools from "./pages/Tools";
import Settings from "./pages/Settings";
import { Avatar, SettingsProvider, useSettings } from "./settings";
import { ConfirmProvider } from "./confirm";
import "./styles.css";

function lastSessionPath(): string {
  try {
    const id = localStorage.getItem("lastSession");
    return id ? `/research/${id}` : "/research";
  } catch { return "/research"; }
}

/** Profile icon menu: settings, plus the workspace screens (Roles, Tools) and the theme. */
function ProfileMenu() {
  const { theme, toggleTheme, profile } = useSettings();
  const pop = usePopover();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const inMenu = ["/roles", "/tools", "/settings"].some((p) => pathname.startsWith(p));
  const go = (to: string) => { pop.setOpen(false); navigate(to); };
  const item = (to: string, icon: React.ReactNode, label: string, hint: string) => (
    <button role="menuitem" className={`menu-item ${pathname.startsWith(to) ? "active" : ""}`} onClick={() => go(to)}>
      <span className="menu-icon">{icon}</span><span><strong>{label}</strong><small>{hint}</small></span>
    </button>
  );
  return (
    <div className="pop-anchor" ref={pop.ref}>
      <button className={`profile-btn ${inMenu ? "on" : ""}`} onClick={() => pop.setOpen(!pop.open)} aria-haspopup="menu"
              aria-expanded={pop.open} title="Profile, settings, roles and tools">
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
          {item("/roles", <Users />, "Roles", "The Lead Analyst and specialists")}
          {item("/tools", <Wrench />, "Tools", "Data sources, connections, models")}
          <div className="menu-sep" />
          <button role="menuitem" className="menu-item" onClick={toggleTheme}>
            <span className="menu-icon">{theme === "dark" ? <Sun /> : <Moon />}</span>
            <span><strong>{theme === "dark" ? "Light mode" : "Dark mode"}</strong></span>
          </button>
        </div>
      )}
    </div>
  );
}

function App() {
  const navigate = useNavigate();
  const [mock, setMock] = useState(false);
  useEffect(() => { api.get("/health").then((h) => setMock(!!h.mock_llm)).catch(() => {}); }, []);
  const links = [["/research", "Research"], ["/skills", "Skills"], ["/scanners", "Scanners"]];
  return (
    <div className="app">
      <header className="appbar">
        <div className="appbar-top">
          <a className="brand" href="/research" onClick={(e) => { e.preventDefault(); navigate(lastSessionPath()); }}>
            <Logo size={30} /><span>Trade <b>Research</b></span>
          </a>
          {mock && (
            <span className="mock-badge" title="MOCK_LLM=1 in .env: answers are canned and free; data tools still run">
              MOCK LLM
            </span>
          )}
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
          <Route path="/roles" element={<Roles />} />
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
