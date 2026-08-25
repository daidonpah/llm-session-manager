// Thin client for the setup backend. Admin credentials (once set) are held in
// memory only and sent as HTTP Basic auth on each request.

let authHeader = null;

export function setAuth(username, password) {
  authHeader = "Basic " + btoa(`${username}:${password}`);
}

export function clearAuth() {
  authHeader = null;
}

function headers(extra = {}) {
  const h = { "Content-Type": "application/json", ...extra };
  if (authHeader) h["Authorization"] = authHeader;
  return h;
}

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON error body */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  status: () => fetch("/api/status").then(handle),
  getConfig: () => fetch("/api/config", { headers: headers() }).then(handle),
  saveConfig: (config) =>
    fetch("/api/config", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ config }),
    }).then(handle),
  saveCert: (slot, cert_pem, key_pem) =>
    fetch("/api/certs", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ slot, cert_pem, key_pem }),
    }).then(handle),
  complete: (username, password) =>
    fetch("/api/complete", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ username, password }),
    }).then(handle),
};

// Stream a model download via SSE (POST body -> EventSource isn't usable, so we
// read the response body as a stream and parse `data:` lines ourselves).
export async function downloadModel(payload, onEvent) {
  const res = await fetch("/api/download", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) {
    throw new Error("download failed to start");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop();
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (line) onEvent(JSON.parse(line.slice(5).trim()));
    }
  }
}
