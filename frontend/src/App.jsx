import { useCallback, useEffect, useState } from "react";
import GenerationStatus from "./components/CaptionBay";
import CaptionPlayer from "./components/CaptionPlayer";
import CueEditor from "./components/CueEditor";
import Library from "./components/Library";
import Queue from "./components/Queue";
import Settings from "./components/Settings";
import { health, subscribe } from "./api";

// Used only until /api/health answers, so the editor never renders against
// undefined rules on a slow first paint.
const FALLBACK_RULES = {
  max_chars_per_line: 42,
  max_lines: 2,
  max_cps: 20,
  min_cue_dur: 0.8,
  max_cue_dur: 7.0,
};

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [info, setInfo] = useState(null);
  const [connected, setConnected] = useState(true);
  const [nudge, setNudge] = useState(0);
  const [editing, setEditing] = useState(null);
  const [previewing, setPreviewing] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    health().then(setInfo).catch(() => setInfo(null));
  }, []);

  useEffect(() => {
    const off = subscribe(
      (data) => {
        setJobs(data.jobs || []);
        setConnected(true);
      },
      () => setConnected(false)
    );
    return off;
  }, [nudge]);

  const refresh = useCallback(() => setNudge((n) => n + 1), []);

  const active =
    jobs.find((j) => j.status === "running" || j.status === "claimed") || null;

  const rules = info?.rules || FALLBACK_RULES;

  if (settingsOpen) {
    return <Settings onClose={() => setSettingsOpen(false)} />;
  }

  if (previewing) {
    const fresh = jobs.find((j) => j.id === previewing.id) || previewing;
    return <CaptionPlayer job={fresh} onClose={() => setPreviewing(null)} />;
  }

  if (editing) {
    // Keep the live job object in sync while the editor is open, so the
    // glossary chips stay accurate if the job record updates underneath.
    const fresh = jobs.find((j) => j.id === editing.id) || editing;
    return (
      <CueEditor job={fresh} rules={rules} onClose={() => setEditing(null)} />
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="wordmark">
          <img src="/mark.png" alt="" className="wordmark-mark" />
          Verbatim
        </div>
        <div className="chips">
          {info && (
            <>
              <span className="chip mono">
                model <strong>{info.model}</strong>
              </span>
              <span className="chip mono">
                <strong>{info.device === "cuda" ? "GPU" : "CPU"}</strong>
                {info.concurrency > 1 ? ` ×${info.concurrency}` : ""}
              </span>
            </>
          )}
          <span className={`chip ${connected ? "" : "offline"}`}>
            {connected ? "live" : "reconnecting"}
          </span>
          <button
            className="chip chip-btn"
            onClick={() => setSettingsOpen(true)}
            title="Settings"
          >
            Settings
          </button>
        </div>
      </header>

      <GenerationStatus job={active} />

      <div className="panes">
        <Library onQueued={refresh} />
        <Queue
          jobs={jobs}
          onChange={refresh}
          onEdit={setEditing}
          onPreview={setPreviewing}
        />
      </div>
    </div>
  );
}
