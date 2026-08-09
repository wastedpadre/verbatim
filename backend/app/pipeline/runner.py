"""Job orchestration.

A small pool of worker threads pull queued jobs, run the pipeline, and write
live progress into a shared in-memory dict that the SSE endpoint polls. State
that matters survives in SQLite; state that's only interesting while a job is
running stays in memory.
"""
import logging
import threading
import time
import traceback
from pathlib import Path

from .. import config, db
from . import audio, clean, glossary, polish, probe, segment, srt, transcribe

log = logging.getLogger("verbatim.runner")

# job_id -> {"progress","stage","caption","cues_done","glossary"}
LIVE: dict[int, dict] = {}
_live_lock = threading.Lock()
_stop = threading.Event()


def live(job_id: int) -> dict:
    with _live_lock:
        return dict(LIVE.get(job_id, {}))


def _set(job_id: int, **fields):
    with _live_lock:
        LIVE.setdefault(job_id, {}).update(fields)


def output_path(src: Path) -> Path:
    return src.with_suffix("").with_name(src.stem + config.OUTPUT_SUFFIX)


def run_job(job: dict):
    job_id = job["id"]
    src = Path(job["path"])
    work = config.WORK_DIR / f"job{job_id}"
    work.mkdir(parents=True, exist_ok=True)

    try:
        db.update(job_id, status="running", stage="probing", progress=0.02)
        _set(job_id, stage="probing", progress=0.02, caption="")

        if not src.exists():
            raise FileNotFoundError(f"{src} is not reachable from the container")

        info = probe.ffprobe(src)
        total = probe.duration(info)
        a_idx, a_note, a_channels = probe.pick_audio(info)
        if a_idx is None:
            raise RuntimeError("No usable audio track in this file")

        db.update(job_id, duration=total, audio_note=a_note)
        _set(job_id, duration=total, audio_note=a_note)

        # --- glossary from the embedded sub track ------------------------
        db.update(job_id, stage="glossary", progress=0.05)
        _set(job_id, stage="glossary", progress=0.05)

        terms = []
        s_idx, _codec = probe.pick_subtitle(info)
        if s_idx is not None:
            ref = audio.extract_subtitle(src, s_idx, work / "ref.srt")
            if ref:
                terms = glossary.from_srt(ref)
        db.update(job_id, glossary=terms)
        _set(job_id, glossary=terms)

        # --- audio -------------------------------------------------------
        db.update(job_id, stage="extracting", progress=0.08)
        _set(job_id, stage="extracting", progress=0.08)
        wav = audio.extract_audio(src, a_idx, work / "audio.wav", a_channels)
        offset = audio.audio_start_offset(src, a_idx)

        # --- transcription ------------------------------------------------
        db.update(job_id, stage="transcribing", progress=0.10)
        _set(job_id, stage="transcribing", progress=0.10)

        def on_progress(seconds_done: float, text: str):
            frac = 0.10 + 0.80 * (seconds_done / total if total else 0)
            _set(job_id, progress=min(frac, 0.90), caption=text,
                 position=seconds_done)
            db.update(job_id, progress=min(frac, 0.90))

        segments = transcribe.transcribe(wav, glossary.to_prompt(terms), on_progress)

        # --- cleanup and shaping ------------------------------------------
        db.update(job_id, stage="cleaning", progress=0.92)
        _set(job_id, stage="cleaning", progress=0.92, caption="")
        segments, stats = clean.apply(segments, terms)

        db.update(job_id, stage="shaping", progress=0.96)
        _set(job_id, stage="shaping", progress=0.96)
        cues = segment.build_cues(segments)
        cues = segment.shift(cues, offset)

        # Optional LLM repair of misheard words. Runs on final cues so it can
        # see readable lines, and never touches timings. If it's disabled or
        # the API is unreachable the cues pass through untouched.
        if config.POLISH_ENABLED and cues:
            db.update(job_id, stage="polishing", progress=0.97)
            _set(job_id, stage="polishing", progress=0.97)
            cues, edits = polish.polish(cues, terms)
            applied = sum(1 for e in edits if e["applied"])
            if applied:
                # Corrected text can overflow the line budget, so re-wrap.
                for c in cues:
                    c["text"] = segment._split_lines(c["text"].replace("\n", " "))
            stats_polish = {"polished": applied,
                            "rejected": len(edits) - applied}
        else:
            stats_polish = {}

        if not cues:
            raise RuntimeError("Nothing survived cleanup, likely the wrong audio track")

        dest = output_path(src)
        if dest.exists() and not config.OVERWRITE:
            dest = dest.with_name(dest.stem + f".{int(time.time())}.srt")
        srt.write(cues, dest)

        stats["cues"] = len(cues)
        stats["terms"] = len(terms)
        stats.update(stats_polish)
        db.update(job_id, status="done", stage="done", progress=1.0,
                  output=str(dest), stats=stats, ended_at=time.time())
        _set(job_id, stage="done", progress=1.0, caption="", stats=stats)
        log.info("job %s wrote %s (%d cues)", job_id, dest, len(cues))

    except Exception as exc:  # noqa: BLE001
        log.error("job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
        db.update(job_id, status="failed", stage="failed",
                  error=str(exc), ended_at=time.time())
        _set(job_id, stage="failed", error=str(exc))
    finally:
        for f in work.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass


def _worker(n: int):
    log.info("worker %d up", n)
    while not _stop.is_set():
        job = db.claim()
        if not job:
            _stop.wait(2.0)
            continue
        run_job(job)


def start():
    for i in range(config.CONCURRENCY):
        threading.Thread(target=_worker, args=(i,), daemon=True, name=f"worker{i}").start()


def stop():
    _stop.set()
