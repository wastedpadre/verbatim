import { useEffect, useRef, useState } from "react";
import { subscribeLogs } from "../api";

/**
 * Live server log, toggled from the topbar.
 *
 * Everything worth diagnosing already goes to the logger — the chosen audio
 * track, an unresolved Sonarr path, a failed polish window — but reading it
 * otherwise means `docker logs`, which is a terminal most Unraid users
 * won't open.
 */
export default function LogPanel({ onClose }) {
  const [lines, setLines] = useState([]);
  const [live, setLive] = useState(true);
  const [paused, setPaused] = useState(false);
  const boxRef = useRef(null);
  // Whether the view is pinned to the newest line. Auto-scrolling while
  // someone is reading back through a failure is the whole reason log
  // viewers are annoying, so scrolling up stops it until they return.
  const pinned = useRef(true);

  useEffect(() => {
    const off = subscribeLogs(
      (batch) => {
        setLive(true);
        if (!paused) {
          setLines((prev) => [...prev, ...batch].slice(-2000));
        }
      },
      () => setLive(false)
    );
    return off;
  }, [paused]);

  useEffect(() => {
    const box = boxRef.current;
    if (box && pinned.current) box.scrollTop = box.scrollHeight;
  }, [lines]);

  const onScroll = () => {
    const box = boxRef.current;
    if (!box) return;
    // 24px of slack: "close enough to the bottom" counts as pinned, so a
    // stray wheel tick doesn't silently disable follow.
    pinned.current = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  };

  return (
    <section className="logs" aria-label="Server log">
      <div className="logs-head">
        <h2>Server log</h2>
        <span className={`chip ${live ? "" : "offline"}`}>
          {live ? "streaming" : "disconnected"}
        </span>
        <div className="logs-actions">
          <button className="mini" onClick={() => setPaused((p) => !p)}>
            {paused ? "Resume" : "Pause"}
          </button>
          <button className="mini" onClick={() => setLines([])}>
            Clear
          </button>
          <button className="mini" onClick={onClose}>
            Hide
          </button>
        </div>
      </div>

      <div className="logs-box mono" ref={boxRef} onScroll={onScroll}>
        {lines.length === 0 && (
          <div className="logs-empty">
            Waiting for output. Queue an episode and the pipeline will report
            here as it works.
          </div>
        )}
        {lines.map((l) => (
          <div key={l.seq} className={`logline lv-${l.level?.toLowerCase()}`}>
            {l.text}
          </div>
        ))}
      </div>
    </section>
  );
}
