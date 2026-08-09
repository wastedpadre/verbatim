import { useEffect, useRef, useState } from "react";

const clock = (s) => {
  if (s === null || s === undefined) return "--:--";
  const t = Math.max(0, Math.floor(s));
  const m = Math.floor(t / 60);
  return `${String(m).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
};

const STAGE_COPY = {
  probing: "Reading tracks",
  glossary: "Collecting names",
  extracting: "Pulling dub audio",
  transcribing: "Transcribing",
  cleaning: "Removing artifacts",
  shaping: "Shaping cues",
  done: "Finished",
  failed: "Stopped",
};

/**
 * Generation status only.
 *
 * This used to double as a caption preview, rendering each line as it came
 * out of the decoder. That conflated two unrelated things: how fast the GPU
 * is working, and whether the captions are any good. Decode order isn't
 * playback order and decode speed isn't reading speed, so it couldn't answer
 * the question people actually wanted it to answer.
 *
 * Reviewing captions now belongs to CaptionPlayer, which plays them on their
 * real timestamps. What's left here is a progress indicator that stays out of
 * the way when nothing is running.
 */
export default function GenerationStatus({ job }) {
  const [glimpse, setGlimpse] = useState("");
  const timer = useRef(null);

  const caption = job?.caption || "";
  const running = job && ["running", "claimed"].includes(job.status);

  useEffect(() => {
    if (!caption) return;
    setGlimpse(caption);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setGlimpse(""), 5000);
    return () => clearTimeout(timer.current);
  }, [caption]);

  if (!running) {
    return (
      <section className="status idle">
        <span className="dot" />
        <span className="status-text">Idle</span>
        <span className="status-hint">
          Pick episodes below to start captioning
        </span>
      </section>
    );
  }

  const pct = Math.round((job.progress || 0) * 100);

  return (
    <section className="status" aria-live="polite">
      <div className="status-row">
        <span className="dot on" />
        <span className="status-text">{STAGE_COPY[job.stage] || "Working"}</span>
        <span className="status-file">{job.title}</span>
        <span className="status-clock mono">
          {clock(job.position)} / {clock(job.duration)}
        </span>
        <span className="status-pct mono">{pct}%</span>
      </div>

      <div className="status-bar">
        <span style={{ width: `${pct}%` }} />
      </div>

      {/* A single most-recent line, purely so you can tell within seconds that
          the right audio track was picked. Not a preview -- that's the player. */}
      {glimpse && <p className="status-glimpse">{glimpse}</p>}

      {job.audio_note && <p className="status-note mono">{job.audio_note}</p>}
    </section>
  );
}
