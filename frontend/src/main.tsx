import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import Research from "./pages/Research";
import Skills from "./pages/Skills";
import Screeners from "./pages/Screeners";
import Roles from "./pages/Roles";
import Tools from "./pages/Tools";
import "./styles.css";

function App() {
  const links = [["/research", "Research"], ["/skills", "Skills"], ["/screeners", "Screeners"],
                 ["/roles", "Roles"], ["/tools", "Tools"]];
  return (
    <div className="app">
      <nav className="topnav">
        <span className="brand">Trade Research</span>
        {links.map(([to, label]) => <NavLink key={to} to={to}>{label}</NavLink>)}
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/research" />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:id" element={<Research />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/screeners" element={<Screeners />} />
          <Route path="/roles" element={<Roles />} />
          <Route path="/tools" element={<Tools />} />
        </Routes>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>,
);
