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
          <button className="mini accent" onClick={() => onPreview?.(job)}>
            Preview playback
          </button>
        )}
        {job.status === "done" && (
          <button className="mini" onClick={() => onEdit?.(job)}>Edit captions</button>
        )}
        {job.status === "done" && (
          <a className="mini" href={srtUrl(job.id)} download>Download SRT</a>
        )}
        {job.status === "failed" && (
          <button className="mini" onClick={() => retry(job.id).then(onChange)}>
            Run again
          </button>
        )}
        {!active && (
          <button className="mini" onClick={() => remove(job.id).then(onChange)}>
            Remove
          </button>
        )}
      </div>
    </article>
  );
}

export default function Queue({ jobs, onChange, onEdit, onPreview }) {
  const pending = jobs.filter((j) => !["done", "failed"].includes(j.status)).length;

  return (
    <section className="pane">
      <div className="pane-head">
        <h2>Queue</h2>
        <span className="count mono">
          {pending ? `${pending} pending` : `${jobs.length} total`}
        </span>
      </div>

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
