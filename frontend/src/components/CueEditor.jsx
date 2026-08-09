import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCues, saveCues } from "../api";

/* --------------------------------------------------------------- timecode */

export const fmt = (s) => {
  const ms = Math.max(0, Math.round(s * 1000));
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const sec = Math.floor((ms % 60000) / 1000);
  const milli = ms % 1000;
  return (
    `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:` +
    `${String(sec).padStart(2, "0")}.${String(milli).padStart(3, "0")}`
  );
};

export const parseTc = (value) => {
  const m = String(value).trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})$/);
  if (!m) return null;
  const [, h, mm, ss, ms] = m;
  return (
    Number(h || 0) * 3600 +
    Number(mm) * 60 +
    Number(ss) +
    Number(ms.padEnd(3, "0")) / 1000
  );
};

/* ------------------------------------------------------------- validation */

/** Mirrors the backend's shaping rules. Rules come from /api/health so the
 *  two can't drift when .env changes. */
export function inspect(cue, next, rules) {
  const problems = [];
  const lines = cue.text.split("\n");
  const flat = cue.text.replace(/\n/g, " ");
  const dur = cue.end - cue.start;

  for (const line of lines) {
    if (line.length > rules.max_chars_per_line) {
      problems.push({ kind: "long", msg: `Line is ${line.length} chars` });
      break;
    }
  }
  if (lines.length > rules.max_lines) {
    problems.push({ kind: "lines", msg: `${lines.length} lines` });
  }
  if (dur <= 0) {
    problems.push({ kind: "timing", msg: "Ends before it starts" });
  } else if (flat.length / dur > rules.max_cps) {
    problems.push({
      kind: "cps",
      msg: `${(flat.length / dur).toFixed(0)} chars/sec`,
    });
  }
  if (dur > 0 && dur < rules.min_cue_dur) {
    problems.push({ kind: "short", msg: `Only ${dur.toFixed(1)}s on screen` });
  }
  if (next && cue.end > next.start) {
    problems.push({ kind: "overlap", msg: "Overlaps the next cue" });
  }
  return problems;
}

/* ------------------------------------------------------------------ rows */

function Row({ cue, next, rules, index, onChange, onMerge, onSplit, onDelete }) {
  const problems = inspect(cue, next, rules);
  const [startText, setStartText] = useState(fmt(cue.start));
  const [endText, setEndText] = useState(fmt(cue.end));

  useEffect(() => setStartText(fmt(cue.start)), [cue.start]);
  useEffect(() => setEndText(fmt(cue.end)), [cue.end]);

  const commit = (field, raw, fallback) => {
    const parsed = parseTc(raw);
    if (parsed === null) {
      field === "start" ? setStartText(fmt(fallback)) : setEndText(fmt(fallback));
      return;
    }
    onChange({ [field]: parsed });
  };

  return (
    <article className={`cue ${problems.length ? "flagged" : ""}`}>
      <div className="cue-gutter">
        <span className="cue-no mono">{String(index + 1).padStart(3, "0")}</span>
      </div>

      <div className="cue-body">
        <div className="cue-times">
          <input
            className="tc mono"
            value={startText}
            onChange={(e) => setStartText(e.target.value)}
            onBlur={() => commit("start", startText, cue.start)}
            aria-label={`Cue ${index + 1} start time`}
          />
          <span className="tc-arrow">→</span>
          <input
            className="tc mono"
            value={endText}
            onChange={(e) => setEndText(e.target.value)}
            onBlur={() => commit("end", endText, cue.end)}
            aria-label={`Cue ${index + 1} end time`}
          />
          <span className="cue-dur mono">{(cue.end - cue.start).toFixed(2)}s</span>

          <div className="cue-tools">
            <button className="tool" onClick={onSplit} title="Split into two cues">
              Split
            </button>
            <button
              className="tool"
              onClick={onMerge}
              disabled={!next}
              title="Join with the next cue"
            >
              Merge
            </button>
            <button className="tool danger" onClick={onDelete} title="Delete cue">
              Delete
            </button>
          </div>
        </div>

        <textarea
          className="cue-text"
          value={cue.text}
          rows={Math.max(2, cue.text.split("\n").length)}
          spellCheck
          onChange={(e) => onChange({ text: e.target.value })}
          aria-label={`Cue ${index + 1} text`}
        />

        <div className="cue-foot">
          {cue.text.split("\n").map((line, i) => (
            <span
              key={i}
              className={`len mono ${
                line.length > rules.max_chars_per_line ? "over" : ""
              }`}
            >
              {line.length}
            </span>
          ))}
          {problems.map((p) => (
            <span key={p.kind} className={`flag ${p.kind}`}>
              {p.msg}
            </span>
          ))}
        </div>
      </div>
    </article>
  );
}

/* ---------------------------------------------------------------- editor */

export default function CueEditor({ job, rules, onClose }) {
  const [cues, setCues] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [find, setFind] = useState("");
  const [replaceWith, setReplaceWith] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    getCues(job.id)
      .then((d) => setCues(d.cues))
      .catch((e) => setError(e.message));
  }, [job.id]);

  const edit = useCallback((i, patch) => {
    setCues((prev) => prev.map((c, n) => (n === i ? { ...c, ...patch } : c)));
    setDirty(true);
  }, []);

  const merge = useCallback((i) => {
    setCues((prev) => {
      if (i + 1 >= prev.length) return prev;
      const joined = {
        start: prev[i].start,
        end: prev[i + 1].end,
        text: `${prev[i].text.replace(/\n/g, " ")} ${prev[i + 1].text.replace(/\n/g, " ")}`
          .replace(/\s{2,}/g, " ")
          .trim(),
      };
      return [...prev.slice(0, i), joined, ...prev.slice(i + 2)];
    });
    setDirty(true);
  }, []);

  const split = useCallback((i) => {
    setCues((prev) => {
      const c = prev[i];
      const flat = c.text.replace(/\n/g, " ").trim();
      const words = flat.split(" ");
      const at = Math.max(1, Math.round(words.length / 2));
      const head = words.slice(0, at).join(" ");
      const tail = words.slice(at).join(" ");
      if (!tail) return prev;
      // Split the duration in proportion to the text, so neither half ends up
      // reading twice as fast as the other.
      const ratio = head.length / flat.length;
      const mid = c.start + (c.end - c.start) * ratio;
      return [
        ...prev.slice(0, i),
        { start: c.start, end: Math.max(c.start + 0.2, mid - 0.02), text: head },
        { start: Math.max(mid, c.start + 0.22), end: c.end, text: tail },
        ...prev.slice(i + 1),
      ];
    });
    setDirty(true);
  }, []);

  const del = useCallback((i) => {
    setCues((prev) => prev.filter((_, n) => n !== i));
    setDirty(true);
  }, []);

  /** The reason this editor exists. When the glossary misses a name, the
   *  decoder gets it wrong the same way in every single cue — so fixing it
   *  is one replace-all, not forty manual edits. */
  const replaceAll = useCallback(() => {
    if (!find.trim()) return;
    const pattern = new RegExp(
      `\\b${find.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`,
      "gi"
    );
    let hits = 0;
    setCues((prev) =>
      prev.map((c) => {
        const next = c.text.replace(pattern, () => {
          hits += 1;
          return replaceWith;
        });
        return next === c.text ? c : { ...c, text: next };
      })
    );
    if (hits) setDirty(true);
    setNotice(
      hits ? `Replaced ${hits} occurrence${hits === 1 ? "" : "s"}` : "No matches"
    );
    setTimeout(() => setNotice(null), 2600);
  }, [find, replaceWith]);

  const save = useCallback(
    async (rewrap = false) => {
      setSaving(true);
      setError(null);
      try {
        const res = await saveCues(job.id, cues, rewrap);
        setDirty(false);
        setNotice(`Saved ${res.cues} cues`);
        if (rewrap) {
          const fresh = await getCues(job.id);
          setCues(fresh.cues);
        }
        setTimeout(() => setNotice(null), 2600);
      } catch (e) {
        setError(e.message);
      } finally {
        setSaving(false);
      }
    },
    [cues, job.id]
  );

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (dirty && !saving) save(false);
      }
      if (e.key === "Escape" && !dirty) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, saving, save, onClose]);

  useEffect(() => {
    const warn = (e) => {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const flagged = useMemo(() => {
    if (!cues) return new Set();
    const s = new Set();
    cues.forEach((c, i) => {
      if (inspect(c, cues[i + 1], rules).length) s.add(i);
    });
    return s;
  }, [cues, rules]);

  const visible = useMemo(() => {
    if (!cues) return [];
    return cues
      .map((c, i) => ({ c, i }))
      .filter(({ i }) => !onlyProblems || flagged.has(i));
  }, [cues, onlyProblems, flagged]);

  const close = () => {
    if (dirty && !window.confirm("You have unsaved changes. Close anyway?")) return;
    onClose();
  };

  return (
    <div className="editor" role="dialog" aria-modal="true" aria-label="Caption editor">
      <header className="editor-head">
        <button className="btn ghost" onClick={close}>
          ← Back
        </button>
        <div className="editor-title">
          <h2>{job.title}</h2>
          <span className="mono">
            {cues ? `${cues.length} cues` : "loading"}
            {flagged.size > 0 && ` · ${flagged.size} to review`}
          </span>
        </div>
        <div className="editor-actions">
          {notice && <span className="notice">{notice}</span>}
          {dirty && <span className="dirty mono">unsaved</span>}
          <button className="btn ghost" onClick={() => save(true)} disabled={saving || !cues}>
            Save &amp; rewrap
          </button>
          <button className="btn" onClick={() => save(false)} disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      <div className="editor-tools">
        <div className="replace">
          <input
            className="field mono"
            placeholder="Find name…"
            value={find}
            onChange={(e) => setFind(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && replaceAll()}
          />
          <span className="tc-arrow">→</span>
          <input
            className="field mono"
            placeholder="Replace with…"
            value={replaceWith}
            onChange={(e) => setReplaceWith(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && replaceAll()}
          />
          <button className="btn ghost" onClick={replaceAll} disabled={!find.trim()}>
            Replace all
          </button>
        </div>

        <button
          className={`toggle ${onlyProblems ? "on" : ""}`}
          onClick={() => setOnlyProblems((v) => !v)}
          aria-pressed={onlyProblems}
        >
          {onlyProblems ? "Showing flagged only" : "Show flagged only"}
        </button>
      </div>

      {job.glossary?.length > 0 && (
        <div className="editor-terms">
          <span className="terms-label mono">Correct spellings from this episode</span>
          <div className="terms">
            {job.glossary.map((t) => (
              <button
                key={t}
                className="term"
                onClick={() => setReplaceWith(t)}
                title={`Use "${t}" as the replacement`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      )}

      {error && <p className="editor-error">{error}</p>}

      <div className="editor-list" ref={scrollRef}>
        {!cues && !error && <div className="empty">Loading captions…</div>}

        {cues && visible.length === 0 && (
          <div className="empty">
            <strong>Nothing flagged</strong>
            Every cue fits the line, timing, and reading-speed rules.
          </div>
        )}

        {visible.map(({ c, i }) => (
          <Row
            key={i}
            index={i}
            cue={c}
            next={cues[i + 1]}
            rules={rules}
            onChange={(patch) => edit(i, patch)}
            onMerge={() => merge(i)}
            onSplit={() => split(i)}
            onDelete={() => del(i)}
          />
        ))}
      </div>
    </div>
  );
}
