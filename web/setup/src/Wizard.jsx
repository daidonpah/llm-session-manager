import React, { useState } from "react";
import { api } from "./api.js";
import { StepModel, StepNginx, StepCerts, StepAdmin, StepDownload } from "./steps.jsx";

const STEPS = ["Model & app", "Reverse proxy", "TLS certs", "Admin", "First model"];

// Multi-step first-run wizard. Config is accumulated in a single object and
// persisted to .env as the user advances; certs and the admin step post
// immediately; the final step downloads the first model with live progress.
export default function Wizard() {
  const [step, setStep] = useState(0);
  const [config, setConfig] = useState({
    LSM_MODEL_BASE_URL: "http://vllm:8000/v1",
    LSM_DEFAULT_MODEL: "qwen3.8-27b",
    LSM_MODEL_API_KEY: "not-needed",
    NGINX_SM_SERVER_NAME: "localhost",
    NGINX_OPENAI_SERVER_NAME: "openai.localhost",
    NGINX_OPENAI_UPSTREAM: "vllm:8000",
    NGINX_HTTPS_PORT: "443",
    HF_TOKEN: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const set = (patch) => setConfig((c) => ({ ...c, ...patch }));

  async function persistAndNext() {
    setBusy(true);
    setErr("");
    try {
      await api.saveConfig(config);
      setStep((s) => s + 1);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const stepProps = { config, set, setErr, setBusy, busy };

  return (
    <div>
      <div className="steps">
        {STEPS.map((label, i) => (
          <span
            key={label}
            className={"step-pill" + (i === step ? " active" : i < step ? " done" : "")}
          >
            {i + 1}. {label}
          </span>
        ))}
      </div>

      <div className="panel">
        {step === 0 && <StepModel {...stepProps} />}
        {step === 1 && <StepNginx {...stepProps} />}
        {step === 2 && <StepCerts {...stepProps} />}
        {step === 3 && <StepAdmin {...stepProps} onDone={() => setStep(4)} />}
        {step === 4 && <StepDownload {...stepProps} />}

        {err && <div className="msg err">{err}</div>}

        {step < 3 && (
          <div className="actions">
            <button
              className="secondary"
              disabled={step === 0 || busy}
              onClick={() => setStep((s) => s - 1)}
            >
              Back
            </button>
            <button disabled={busy} onClick={persistAndNext}>
              {busy ? "Saving…" : "Save & continue"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
