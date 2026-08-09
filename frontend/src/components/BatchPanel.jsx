import { useState } from "react";
import { enqueue, scanFolder } from "../api";

const REASON_ORDER = ["already captioned", "already has", "no English audio track", "already queued"];

function reasonLabel(key) {
  if (key.startsWith("already has")) return "Release ships its own dubtitles";
  if (key === "already captioned") return "Already captioned by Verbatim";
  if (key === "no English audio track") return "No English audio";
  if (key === "already queued") return "Already in the queue";
  return key;
}

/**
 * Pre-flight before queueing a whole folder.
 *
 * The count alone isn't enough — "will process 6 of 12" invites the question
 * "why not the other 6?", and the answers are genuinely different in kind.
 * A file skipped because the release already ships dubtitles is a success;
 * one skipped for no English audio might mean a mistagged release you'd want
 * to force through.
 */
export default function BatchPanel({ path, onQueued, onClose }) {
  const [scan, setScan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [showAll, setShowAll] = useState(false);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setScan(await scanFolder(path));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const queue = async () => {
    setBusy(true);
    try {
      await enqueue(scan.paths);
      onQueued?.();
      onClose?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const skipped = scan?.entries?.filter((e) => e.status === "skip") || [];

  return (
    <div className="batch">
      <div className="batch-head">
        <h3>Caption everything in this folder</h3>
        <button className="tool" onClick={onClose}>Close</button>
      </div>

      {!scan && (
        <div className="batch-intro">
          <p>
            Checks every episode below this folder, including subfolders, and
            reports what it would process before anything is queued.
          </p>
          <button className="btn" onClick={run} disabled={busy}>
            {busy ? "Checking episodes…" : "Check this folder"}
          </button>
        </div>
      )}

      {error && <p className="editor-error">{error}</p>}

      {scan && (
        <>
          <div className="batch-summary">
            <div className="batch-stat ready">
              <strong>{scan.ready}</strong>
              <span>to caption</span>
            </div>
            <div className="batch-stat">
              <strong>{scan.skipped}</strong>
              <span>skipped</span>
            </div>
            <div className="batch-stat">
              <strong>{scan.total}</strong>
              <span>found</span>
            </div>
            {scan.estimated_minutes > 0 && (
              <div className="batch-stat">
                <strong>~{scan.estimated_minutes}m</strong>
                <span>estimated</span>
              </div>
            )}
          </div>

          {Object.keys(scan.reasons).length > 0 && (
            <ul className="batch-reasons">
              {Object.entries(scan.reasons)
                .sort((a, b) => REASON_ORDER.indexOf(a[0]) - REASON_ORDER.indexOf(b[0]))
                .map(([key, n]) => (
                  <li key={key}>
                    <span className="rcount">{n}</span>
                    {reasonLabel(key)}
                  </li>
                ))}
            </ul>
          )}

          {skipped.length > 0 && (
            <button className="tool" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Hide skipped files" : `Show ${skipped.length} skipped files`}
            </button>
          )}

          {showAll && (
            <div className="batch-list">
              {skipped.map((e) => (
                <div className="batch-row" key={e.path}>
                  <span className="batch-name">{e.name}</span>
                  <span className="batch-reason">{e.reason}</span>
                </div>
              ))}
            </div>
          )}

          <div className="batch-actions">
            <button className="btn" onClick={queue} disabled={busy || !scan.ready}>
              {scan.ready
                ? `Queue ${scan.ready} episode${scan.ready === 1 ? "" : "s"}`
                : "Nothing to queue"}
            </button>
            <button className="btn ghost" onClick={run} disabled={busy}>
              Re-check
            </button>
          </div>
        </>
      )}
    </div>
  );
}
