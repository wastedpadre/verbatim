const json = async (res) => {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
};

export const browse = (path) =>
  fetch(`/api/library${path ? `?path=${encodeURIComponent(path)}` : ""}`).then(json);

export const enqueue = (paths) =>
  fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  }).then(json);

export const health = () => fetch("/api/health").then(json);

export const getSettings = () => fetch("/api/settings").then(json);

export const saveSettings = (values) =>
  fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  }).then(json);

export const testPolish = () =>
  fetch("/api/settings/test-polish", { method: "POST" }).then(json);

export const listPolishModels = () => fetch("/api/settings/models").then(json);
export const retry = (id) => fetch(`/api/jobs/${id}/retry`, { method: "POST" }).then(json);
export const remove = (id) => fetch(`/api/jobs/${id}`, { method: "DELETE" }).then(json);
export const srtUrl = (id) => `/api/jobs/${id}/srt`;

export const getCues = (id) => fetch(`/api/jobs/${id}/cues`).then(json);

export const scanFolder = (path) =>
  fetch(`/api/scan?path=${encodeURIComponent(path)}`).then(json);

export const saveCues = (id, cues, rewrap = false) =>
  fetch(`/api/jobs/${id}/cues`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cues, rewrap }),
  }).then(json);

/** Subscribe to queue state. Returns an unsubscribe function. */
export function subscribe(onData, onError) {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    try {
      onData(JSON.parse(e.data));
    } catch {
      /* keepalive comment frames land here; ignore */
    }
  };
  es.onerror = () => onError?.();
  return () => es.close();
}
