import Tip from "./Tip";
import { remove, retry, srtUrl } from "../api";

const STATE_LABEL = {
  queued: "queued",
  claimed: "starting",
  running: "running",
  done: "done",
  failed: "failed",
};

const stateClass = (s) =>
  s === "done" ? "done" : s === "failed" ? "failed" : s === "running" || s === "claimed" ? "running" : "";

const elapsed = (job) => {
  if (!job.started_at) return null;
  const end = job.ended_at || Date.now() / 1000;
  const secs = Math.max(0, Math.round(end - job.started_at));
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
};

function Job({ job, onChange, onEdit, onPreview }) {
  const pct = Math.round((job.progress || 0) * 100);
  const active = job.status === "running" || job.status === "claimed";
  const stats = job.stats;

  return (
    <article className="job">
      <div className="job-top">
        <span className="job-name" title={job.path}>{job.title}</span>
        <span className={`state ${stateClass(job.status)}`}>
          {STATE_LABEL[job.status] || job.status}
        </span>
      </div>

      {active && (
        <div className="job-bar">
          <span style={{ width: `${pct}%` }} />
        </div>
      )}

      <div className="job-info mono">
        {active && <span>{job.stage}</span>}
        {active && <span className="sep">·</span>}
        {elapsed(job) && <span>{elapsed(job)}</span>}
        {stats && (
          <>
            <span className="sep">·</span>
            <span>{stats.cues} cues</span>
            {stats.dropped > 0 && (
              <>
                <span className="sep">·</span>
                <span>{stats.dropped} artifacts removed</span>
              </>
            )}
            {stats.renamed > 0 && (
              <>
                <span className="sep">·</span>
                <span>{stats.renamed} names corrected</span>
              </>
            )}
          </>
        )}
      </div>

      {job.error && <p className="job-err">{job.error}</p>}

      {job.glossary?.length > 0 && (
        <div className="terms">
          {job.glossary.slice(0, 12).map((t) => (
            <span className="term" key={t}>{t}</span>
          ))}
          {job.glossary.length > 12 && (
            <span className="term" style={{ opacity: 0.6 }}>
              +{job.glossary.length - 12}
            </span>
          )}
        </div>
      )}

      <div className="job-actions">
        {job.status === "done" && (
          <Tip text="Plays the cues on their real timestamps so you can check the timing without opening the video.">
            <button className="mini accent" onClick={() => onPreview?.(job)}>
              Preview playback
            </button>
          </Tip>
        )}
        {job.status === "done" && (
          <Tip text="Find-and-replace, split, merge and retime. Saves straight over the .srt next to your video.">
            <button className="mini" onClick={() => onEdit?.(job)}>Edit captions</button>
          </Tip>
        )}
        {job.status === "done" && (
          <Tip text="Download a copy. The file is already written next to the video — this is only for taking it elsewhere.">
            <a className="mini" href={srtUrl(job.id)} download>Download SRT</a>
          </Tip>
        )}
        {job.status === "failed" && (
          <Tip text="Re-queue this episode. Settings changes since it failed will apply.">
            <button className="mini" onClick={() => retry(job.id).then(onChange)}>
              Run again
            </button>
          </Tip>
        )}
        {!active && (
          <Tip text="Drops the job from this list. Any .srt already written is left alone.">
            <button className="mini" onClick={() => remove(job.id).then(onChange)}>
              Remove
            </button>
          </Tip>
        )}
      </div>
    </article>
  );
}

export default function Queue({ jobs, onChange, onEdit, onPreview }) {
  const pending = jobs.filter((j) => !["done", "failed"].includes(j.status)).length;
  const anyDone = jobs.some((j) => j.status === "done");

  return (
    <section className="pane">
      <div className="pane-head">
        <h2>Queue</h2>
        <span className="count mono">
          {pending ? `${pending} pending` : `${jobs.length} total`}
        </span>
      </div>

      {/* Shown once a file exists on disk, because that is the moment the
          question comes up. Plex finds the sidecar but does not switch to
          it, and "the subtitles didn't work" is otherwise where this ends. */}
      {anyDone && (
        <p className="pane-note">
          The <code>.srt</code> is written next to the video. Jellyfin selects it
          on its own; <strong>Plex does not</strong> — play the episode, open the
          subtitle menu and pick{" "}
          <strong>English (SRT External)</strong>. Set it as the default for the
          series under <em>Settings → Subtitles</em> if you don't want to repeat it.
        </p>
      )}

      <div className="rows" style={{ maxHeight: 560 }}>
        {jobs.length ? (
          jobs.map((j) => <Job
              key={j.id}
              job={j}
              onChange={onChange}
              onEdit={onEdit}
              onPreview={onPreview}
            />)
        ) : (
          <div className="empty">
            <strong>No jobs yet</strong>
            Pick episodes on the left and they'll show up here.
          </div>
        )}
      </div>
    </section>
  );
}
