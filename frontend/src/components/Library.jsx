import { useCallback, useEffect, useState } from "react";
import BatchPanel from "./BatchPanel";
import { browse, enqueue } from "../api";

const gb = (bytes) =>
  bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;

const FolderIcon = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M1.5 4a1 1 0 0 1 1-1h3.1a1 1 0 0 1 .7.3L7.5 4.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1V4Z"
      stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
  </svg>
);

const FilmIcon = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <rect x="1.6" y="3" width="12.8" height="10" rx="1.2" stroke="currentColor" strokeWidth="1.2" />
    <path d="M5 3v10M11 3v10" stroke="currentColor" strokeWidth="1.2" />
  </svg>
);

const Check = () => (
  <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
    <path d="M2.5 6.3 4.8 8.6 9.5 3.6" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export default function Library({ onQueued }) {
  const [state, setState] = useState({ entries: [], path: null, parent: null });
  const [picked, setPicked] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [batching, setBatching] = useState(false);

  const load = useCallback(async (path) => {
    setError(null);
    try {
      setState(await browse(path));
      setPicked(new Set());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load(null);
  }, [load]);

  const toggle = (path) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });

  const videos = state.entries.filter((e) => e.kind === "video");

  const selectAll = () =>
    setPicked(
      picked.size === videos.length
        ? new Set()
        : new Set(videos.map((v) => v.path))
    );

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await enqueue([...picked]);
      setPicked(new Set());
      onQueued?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const crumbs = state.path ? state.path.split("/").filter(Boolean) : [];

  return (
    <section className="pane">
      <div className="pane-head">
        <h2>Library</h2>
        <span className="count mono">
          {videos.length} video{videos.length === 1 ? "" : "s"}
        </span>
      </div>

      <nav className="crumbs mono" aria-label="Folder path">
        <button onClick={() => load(null)}>roots</button>
        {crumbs.map((part, i) => (
          <span key={i}>
            <span style={{ color: "var(--line)" }}> / </span>
            <button onClick={() => load("/" + crumbs.slice(0, i + 1).join("/"))}>
              {part}
            </button>
          </span>
        ))}
      </nav>

      <div className="rows">
        {state.parent && (
          <button className="row" onClick={() => load(state.parent)}>
            <span className="row-icon"><FolderIcon /></span>
            <span className="row-name" style={{ color: "var(--dim)" }}>..</span>
          </button>
        )}

        {state.entries.map((e) =>
          e.kind === "dir" ? (
            <button key={e.path} className="row" onClick={() => load(e.path)}>
              <span className="row-icon"><FolderIcon /></span>
              <span className="row-name">{e.name}</span>
            </button>
          ) : (
            <button
              key={e.path}
              className={`row ${picked.has(e.path) ? "picked" : ""}`}
              onClick={() => toggle(e.path)}
              aria-pressed={picked.has(e.path)}
            >
              <span className={`tick ${picked.has(e.path) ? "on" : ""}`}>
                {picked.has(e.path) && <Check />}
              </span>
              <span className="row-icon"><FilmIcon /></span>
              <span className="row-name">{e.name}</span>
              {e.has_subs && <span className="badge-has">CC</span>}
              <span className="row-meta">{gb(e.size)}</span>
            </button>
          )
        )}

        {!state.entries.length && (
          <div className="empty">
            <strong>Nothing here</strong>
            Check that your media share is mounted into the container.
          </div>
        )}
      </div>

      {batching && state.path && (
        <BatchPanel
          path={state.path}
          onQueued={onQueued}
          onClose={() => setBatching(false)}
        />
      )}

      <div className="pane-foot">
        <button className="btn" disabled={!picked.size || busy} onClick={submit}>
          {busy
            ? "Queueing…"
            : `Caption ${picked.size || ""} episode${picked.size === 1 ? "" : "s"}`.trim()}
        </button>
        {videos.length > 0 && (
          <button className="btn ghost" onClick={selectAll}>
            {picked.size === videos.length ? "Clear" : "Select all"}
          </button>
        )}
        {state.path && !batching && (
          <button className="btn ghost" onClick={() => setBatching(true)}>
            Whole folder…
          </button>
        )}
        {error && <span style={{ color: "var(--bad)", fontSize: 12 }}>{error}</span>}
      </div>
    </section>
  );
}
