import React, { useState } from "react";
import { api, setAuth, clearAuth } from "./api.js";

// Shown after setup is complete. The one-time wizard is sealed server-side; this
// gates the (future) admin surface behind the admin credentials from setup.
export default function AdminLogin() {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [state, setState] = useState({ msg: "", ok: false });

  async function login(e) {
    e.preventDefault();
    setAuth(username, password);
    try {
      await api.getConfig();
      setState({ msg: "Signed in. Admin console coming soon.", ok: true });
    } catch (err) {
      clearAuth();
      setState({ msg: err.status === 401 ? "Invalid credentials." : err.message, ok: false });
    }
  }

  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>Admin sign in</h2>
      <p className="hint">
        Setup is complete. Sign in with the admin account you created.
      </p>
      <form onSubmit={login}>
        <label>Username</label>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <label>Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <div className="actions">
          <span />
          <button type="submit">Sign in</button>
        </div>
      </form>
      {state.msg && <div className={"msg " + (state.ok ? "ok" : "err")}>{state.msg}</div>}
    </div>
  );
}
