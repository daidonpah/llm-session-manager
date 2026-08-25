import React, { useState } from "react";
import { api, setAuth, downloadModel } from "./api.js";

const F = ({ label, hint, ...p }) => (
  <>
    <label>{label}</label>
    <input {...p} />
    {hint && <div className="hint">{hint}</div>}
  </>
);

export function StepModel({ config, set }) {
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Model & app</h2>
      <F
        label="Model base URL (upstream OpenAI-compatible server)"
        value={config.LSM_MODEL_BASE_URL}
        onChange={(e) => set({ LSM_MODEL_BASE_URL: e.target.value })}
        hint="Use http://vllm:8000/v1 for the bundled DGX Spark vLLM service."
      />
      <F
        label="Default model id"
        value={config.LSM_DEFAULT_MODEL}
        onChange={(e) => set({ LSM_DEFAULT_MODEL: e.target.value })}
        hint="Must match the id the upstream advertises (vLLM --served-model-name)."
      />
      <F
        label="Model API key (if the upstream needs one)"
        value={config.LSM_MODEL_API_KEY}
        onChange={(e) => set({ LSM_MODEL_API_KEY: e.target.value })}
      />
    </div>
  );
}

export function StepNginx({ config, set }) {
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Reverse proxy (nginx)</h2>
      <div className="row">
        <div>
          <F
            label="Session-manager hostname"
            value={config.NGINX_SM_SERVER_NAME}
            onChange={(e) => set({ NGINX_SM_SERVER_NAME: e.target.value })}
          />
        </div>
        <div>
          <F
            label="Raw OpenAI hostname"
            value={config.NGINX_OPENAI_SERVER_NAME}
            onChange={(e) => set({ NGINX_OPENAI_SERVER_NAME: e.target.value })}
          />
        </div>
      </div>
      <F
        label="Raw OpenAI upstream (host:port)"
        value={config.NGINX_OPENAI_UPSTREAM}
        onChange={(e) => set({ NGINX_OPENAI_UPSTREAM: e.target.value })}
        hint="Where the raw passthrough vhost points (e.g. vllm:8000)."
      />
      <F
        label="HTTPS port"
        value={config.NGINX_HTTPS_PORT}
        onChange={(e) => set({ NGINX_HTTPS_PORT: e.target.value })}
      />
    </div>
  );
}

export function StepCerts({ setErr, setBusy, busy }) {
  const [slot, setSlot] = useState("shared");
  const [cert, setCert] = useState("");
  const [key, setKey] = useState("");
  const [ok, setOk] = useState("");

  async function save() {
    setBusy(true);
    setErr("");
    setOk("");
    try {
      const r = await api.saveCert(slot, cert, key);
      setOk(`Wrote ${r.cert} + ${r.key}.`);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>TLS certificates</h2>
      <p className="hint">
        Paste PEM text. Use one shared cert (both hostnames via SAN) or a
        separate cert per vhost. Optional — skip if you terminate TLS elsewhere.
      </p>
      <label>Cert slot</label>
      <select value={slot} onChange={(e) => setSlot(e.target.value)}>
        <option value="shared">Shared (server.crt/key)</option>
        <option value="sm">Session manager (sm.crt/key)</option>
        <option value="openai">Raw OpenAI (openai.crt/key)</option>
      </select>
      <label>Certificate (PEM)</label>
      <textarea value={cert} onChange={(e) => setCert(e.target.value)}
        placeholder="-----BEGIN CERTIFICATE-----" />
      <label>Private key (PEM)</label>
      <textarea value={key} onChange={(e) => setKey(e.target.value)}
        placeholder="-----BEGIN PRIVATE KEY-----" />
      <div className="actions">
        <span />
        <button disabled={busy || !cert || !key} onClick={save}>
          {busy ? "Writing…" : "Write cert"}
        </button>
      </div>
      {ok && <div className="msg ok">{ok}</div>}
    </div>
  );
}

export function StepAdmin({ setErr, setBusy, busy, onDone }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  async function complete() {
    if (password.length < 8) return setErr("Password must be at least 8 characters.");
    if (password !== confirm) return setErr("Passwords do not match.");
    setBusy(true);
    setErr("");
    try {
      await api.complete(username, password);
      // From now on the API requires auth; keep credentials for the last step.
      setAuth(username, password);
      onDone();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Admin account</h2>
      <p className="hint">
        Creating this seals the one-time setup. Afterwards the webapp requires
        these credentials to sign in.
      </p>
      <F label="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
      <label>Password</label>
      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      <label>Confirm password</label>
      <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <div className="actions">
        <span />
        <button disabled={busy} onClick={complete}>
          {busy ? "Saving…" : "Create admin & continue"}
        </button>
      </div>
    </div>
  );
}

export function StepDownload({ config, setErr }) {
  const [repo, setRepo] = useState("Pilcothink/Qwen3.8-27B-MixedInt4-AutoRound");
  const [pct, setPct] = useState(0);
  const [line, setLine] = useState("");
  const [state, setState] = useState("idle"); // idle | running | done | error

  async function start() {
    setState("running");
    setErr("");
    setPct(0);
    try {
      await downloadModel({ repo_id: repo, model_type: "model" }, (ev) => {
        if (ev.type === "progress") {
          setPct(ev.percent);
          const mb = (n) => (n / 1048576).toFixed(0);
          setLine(`${ev.percent}% · ${mb(ev.downloaded_bytes)}/${mb(ev.total_bytes)} MB`);
        } else if (ev.type === "done") {
          setPct(100);
          setLine(`Downloaded to ${ev.local_dir}`);
          setState("done");
        } else if (ev.type === "error") {
          setErr(ev.message);
          setState("error");
        }
      });
      setState((s) => (s === "running" ? "done" : s));
    } catch (e) {
      setErr(e.message);
      setState("error");
    }
  }

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Download your first model</h2>
      <p className="hint">
        Optional. Fetches the repo into ./assets so vLLM can serve it. The
        default is the DGX Spark flagship model.
      </p>
      <F label="HuggingFace repo id" value={repo} onChange={(e) => setRepo(e.target.value)} />
      <div className="actions">
        <span />
        <button disabled={state === "running"} onClick={start}>
          {state === "running" ? "Downloading…" : "Download"}
        </button>
      </div>
      {(state !== "idle") && (
        <>
          <div className="bar" style={{ marginTop: "1rem" }}>
            <span style={{ width: `${pct}%` }} />
          </div>
          <div className="msg">{line}</div>
        </>
      )}
      {state === "done" && (
        <div className="msg ok">
          Setup complete. Start the stack, then sign in with your admin account.
        </div>
      )}
    </div>
  );
}
