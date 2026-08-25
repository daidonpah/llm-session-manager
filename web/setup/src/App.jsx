import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import Wizard from "./Wizard.jsx";
import AdminLogin from "./AdminLogin.jsx";

// Top-level router: before setup is complete, run the wizard. Afterwards the
// one-time wizard is sealed server-side, so show the admin login instead.
export default function App() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .status()
      .then(setStatus)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div className="app">
      <h1>llm-session-manager</h1>
      <p className="sub">Batteries-included setup</p>
      {error && <div className="msg err">Cannot reach setup API: {error}</div>}
      {!status && !error && <div className="msg">Loading…</div>}
      {status && !status.configured && <Wizard />}
      {status && status.configured && <AdminLogin />}
    </div>
  );
}
