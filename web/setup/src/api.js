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
//
// Parsing is deliberately tolerant: events are separated by a blank line, line
// endings may be \n or \r\n, an event may carry multiple `data:` lines (joined
// with newlines per the SSE spec), and the final event may arrive without a
// trailing blank line -- so we flush whatever remains once the stream ends.
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

  const emit = (block) => {
    const data = block
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, "")) // strip one optional leading space
      .join("\n");
    if (!data) return;
    try {
      onEvent(JSON.parse(data));
    } catch {
      /* ignore keep-alive comments / partial or non-JSON frames */
    }
  };

  const drain = () => {
    // Normalize CRLF so a single split handles both line-ending styles.
    const parts = buffer.replace(/\r\n/g, "\n").split("\n\n");
    buffer = parts.pop(); // last part may be an incomplete event; keep it buffered
    for (const block of parts) emit(block);
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    drain();
  }
  // Flush a final event that had no trailing blank line.
  buffer += decoder.decode();
  if (buffer.trim()) emit(buffer);
}
