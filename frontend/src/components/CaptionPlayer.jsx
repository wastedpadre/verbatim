import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCues } from "../api";

const clock = (s) => {
  const t = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
};

/** Which cue is on screen at time t. Cues are sorted and non-overlapping
 *  (the server guarantees both), so a binary search keeps this cheap even
 *  at 60fps on a 400-cue episode. */
function cueAt(cues, t) {
  let lo = 0;
  let hi = cues.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const c = cues[mid];
    if (t < c.start) hi = mid - 1;
    else if (t > c.end) lo = mid + 1;
    else return mid;
  }
  return -1;
}

/**
 * Plays generated captions back on their real SRT timestamps.
 *
 * This is deliberately decoupled from transcription progress. Watching cues
 * stream past at decode speed tells you nothing about whether they're
 * readable; what matters is whether a line is on screen long enough to read
 * at the moment it's spoken. So this runs on a virtual clock at wall-clock
 * speed, exactly as the file will behave in Plex.
 */
export default function CaptionPlayer({ job, onClose }) {
  const [cues, setCues] = useState(null);
  const [error, setError] = useState(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const raf = useRef(null);
  const last = useRef(0);

  useEffect(() => {
    getCues(job.id)
      .then((d) => setCues(d.cues))
      .catch((e) => setError(e.message));
  }, [job.id]);

  const duration = useMemo(
    () => (cues?.length ? cues[cues.length - 1].end + 2 : 0),
    [cues]
  );

  // Virtual transport. rAF rather than setInterval so the clock stays honest
  // when the tab is throttled instead of drifting behind.
  useEffect(() => {
    if (!playing || !duration) return;
    last.current = performance.now();
    const tick = (now) => {
      const dt = ((now - last.current) / 1000) * rate;
      last.current = now;
      setT((prev) => {
        const next = prev + dt;
        if (next >= duration) {
          setPlaying(false);
          return duration;
        }
        return next;
      });
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, duration, rate]);

  const activeIdx = cues ? cueAt(cues, t) : -1;
  const active = activeIdx >= 0 ? cues[activeIdx] : null;

  const jump = useCallback(
    (delta) => setT((prev) => Math.min(Math.max(prev + delta, 0), duration)),
    [duration]
  );

  // Step to the next/previous cue boundary, far more useful than blind
  // seeking when you're checking whether a specific line reads correctly.
  const step = useCallback(
    (dir) => {
      if (!cues?.length) return;
      const next = dir > 0
        ? cues.find((c) => c.start > t + 0.01)
        : [...cues].reverse().find((c) => c.start < t - 0.01);
      if (next) setT(next.start);
    },
    [cues, t]
  );

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "INPUT") return;
      if (e.code === "Space") { e.preventDefault(); setPlaying((p) => !p); }
      if (e.key === "ArrowRight") { e.preventDefault(); e.shiftKey ? step(1) : jump(5); }
      if (e.key === "ArrowLeft") { e.preventDefault(); e.shiftKey ? step(-1) : jump(-5); }
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [jump, step, onClose]);

  const gaps = useMemo(() => {
    if (!cues?.length || !duration) return [];
    return cues.map((c) => ({
      left: (c.start / duration) * 100,
      width: Math.max(((c.end - c.start) / duration) * 100, 0.12),
    }));
  }, [cues, duration]);

  return (
    <div className="player" role="dialog" aria-modal="true" aria-label="Caption preview">
      <header className="player-head">
        <button className="btn ghost" onClick={onClose}>← Back</button>
        <div className="player-title">
          <h2>{job.title}</h2>
          <span>{cues ? `${cues.length} cues` : "loading"}</span>
        </div>
      </header>

      {error && <p className="editor-error">{error}</p>}

      <div className="stage">
        {active ? (
          <p className="stage-caption">
            {active.text.split("\n").map((line, i) => (
              <span key={i}>{line}<br /></span>
            ))}
          </p>
        ) : (
          <p className="stage-empty">
            {cues ? (playing ? "" : "Press play to preview") : "Loading captions…"}
          </p>
        )}
      </div>

      <div className="transport">
        <div className="track">
          {/* Every cue as a tick, so gaps in dialogue are visible at a glance
              a long empty stretch is either a silent scene or missed lines. */}
          <div className="track-cues">
            {gaps.map((g, i) => (
              <span
                key={i}
                className={`tick-cue ${i === activeIdx ? "on" : ""}`}
                style={{ left: `${g.left}%`, width: `${g.width}%` }}
              />
            ))}
          </div>
          <input
            className="scrub"
            type="range"
            min={0}
            max={duration || 1}
            step={0.05}
            value={t}
            onChange={(e) => setT(Number(e.target.value))}
            aria-label="Seek"
          />
          <div className="track-fill" style={{ width: `${(t / (duration || 1)) * 100}%` }} />
        </div>

        <div className="controls">
          <button className="ctl" onClick={() => step(-1)} title="Previous cue (Shift+←)">⏮</button>
          <button className="ctl" onClick={() => jump(-5)} title="Back 5s (←)">−5s</button>
          <button className="ctl primary" onClick={() => setPlaying((p) => !p)}>
            {playing ? "Pause" : "Play"}
          </button>
          <button className="ctl" onClick={() => jump(5)} title="Forward 5s (→)">+5s</button>
          <button className="ctl" onClick={() => step(1)} title="Next cue (Shift+→)">⏭</button>

          <span className="player-clock mono">
            {clock(t)} / {clock(duration)}
          </span>

          <select
            className="rate"
            value={rate}
            onChange={(e) => setRate(Number(e.target.value))}
            aria-label="Playback speed"
          >
            <option value={0.5}>0.5×</option>
            <option value={1}>1×</option>
            <option value={1.5}>1.5×</option>
            <option value={2}>2×</option>
          </select>
        </div>

        <p className="player-hint">
          Space to play or pause · arrows to seek · shift and arrows to step cue by cue
        </p>
      </div>
    </div>
  );
}
