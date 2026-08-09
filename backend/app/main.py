import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from concurrent.futures import ThreadPoolExecutor

from . import config, db, settings
from .pipeline import probe, providers, runner, segment, srt

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("verbatim")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Log each startup step. A previous build could hang inside db.init()
    # with no output at all, which made a stuck container indistinguishable
    # from a slow one.
    log.info("starting up")
    db.init()
    settings.load()
    runner.start()
    log.info("media roots: %s", ", ".join(str(p) for p in config.MEDIA_ROOTS))
    yield
    runner.stop()


app = FastAPI(title="Verbatim", lifespan=lifespan)


# ---------------------------------------------------------------- library

def _safe(path: Path) -> Path:
    """Refuse anything outside the mounted media roots."""
    resolved = path.resolve()
    if not any(str(resolved).startswith(str(root.resolve())) for root in config.MEDIA_ROOTS):
        raise HTTPException(400, "Path is outside the mounted media roots")
    return resolved


@app.get("/api/library")
def library(path: str | None = None):
    """Browse one directory level at a time. Scanning a whole anime library
    up front is slow and the tree view is nicer anyway."""
    roots = [r for r in config.MEDIA_ROOTS if r.exists()]

    if path:
        base = _safe(Path(path))
    elif len(roots) == 1:
        # With a single mount, showing a folder list containing exactly one
        # entry is pure friction. Drop straight into it.
        base = roots[0]
    else:
        return {
            "path": None, "parent": None, "root": None,
            "entries": [{"name": str(r), "path": str(r), "kind": "dir"} for r in roots],
        }

    if not base.is_dir():
        raise HTTPException(404, "Not a directory")

    entries = []
    for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            entries.append({"name": child.name, "path": str(child), "kind": "dir"})
        elif child.suffix.lower() in config.VIDEO_EXTS:
            out = runner.output_path(child)
            entries.append({
                "name": child.name,
                "path": str(child),
                "kind": "video",
                "size": child.stat().st_size,
                "has_subs": out.exists(),
            })

    # Which root are we under? The UI uses this to show a readable breadcrumb
    # instead of the full container path.
    here = base.resolve()
    root = next((r for r in roots if str(here).startswith(str(r.resolve()))), None)

    at_root = root is not None and here == root.resolve()
    parent = None if at_root else str(base.parent)

    return {
        "path": str(base),
        "parent": parent,
        "root": str(root) if root else None,
        "entries": entries,
    }


@app.get("/api/scan")
def scan(path: str, recursive: bool = True):
    """Pre-flight for queueing a folder.

    Every file gets one ffprobe so we can say *why* something will be skipped
    rather than just how many. Three reasons matter, and the third is the one
    that surprised us in testing: some releases already ship a dub-synced
    subtitle track, and regenerating that is six minutes of GPU time spent
    reproducing something the file already contains.
    """
    base = _safe(Path(path))
    if not base.is_dir():
        raise HTTPException(400, "Not a directory")

    files = [
        p for p in sorted(base.rglob("*") if recursive else base.iterdir())
        if p.is_file() and p.suffix.lower() in config.VIDEO_EXTS
    ]

    def inspect(p: Path) -> dict:
        entry = {"path": str(p), "name": p.name}

        if runner.output_path(p).exists():
            entry.update(status="skip", reason="already captioned")
            return entry
        if db.has_active(str(p)):
            entry.update(status="skip", reason="already queued")
            return entry

        try:
            st = p.stat()
        except OSError as exc:
            entry.update(status="skip", reason=f"unreadable: {exc}")
            return entry

        info = db.scan_cache_get(str(p), st.st_mtime, st.st_size)
        if info is None:
            info = probe.classify(p)
            db.scan_cache_put(str(p), st.st_mtime, st.st_size, info)

        if not info.get("ok"):
            entry.update(status="skip", reason=info.get("reason", "unreadable"))
        elif info.get("embedded_dubtitle"):
            entry.update(status="skip",
                         reason=f"already has {info['embedded_dubtitle']}")
        elif not info.get("english_audio"):
            entry.update(status="skip", reason="no English audio track")
        else:
            entry.update(status="ready", reason=info.get("audio_note", ""),
                         duration=info.get("duration", 0))
        return entry

    # Probing is IO-bound and each file is independent, so fan out. A season
    # folder returns in about a second instead of ten.
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(inspect, files))

    ready = [e for e in entries if e["status"] == "ready"]
    skipped = [e for e in entries if e["status"] == "skip"]

    reasons: dict[str, int] = {}
    for e in skipped:
        key = e["reason"].split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1

    return {
        "folder": base.name,
        "total": len(entries),
        "ready": len(ready),
        "skipped": len(skipped),
        "reasons": reasons,
        "estimated_minutes": round(sum(e.get("duration", 0) for e in ready) / 60 * 0.28),
        "entries": entries[:400],
        "paths": [e["path"] for e in ready],
    }


# ------------------------------------------------------------------- jobs

class EnqueueBody(BaseModel):
    paths: list[str]


@app.post("/api/jobs")
def create_jobs(body: EnqueueBody):
    created, skipped = [], []
    for raw in body.paths:
        p = _safe(Path(raw))
        if not p.is_file():
            skipped.append({"path": raw, "reason": "not found"})
            continue
        if db.has_active(str(p)):
            skipped.append({"path": raw, "reason": "already queued"})
            continue
        created.append(db.enqueue(p, p.stem))
    return {"created": created, "skipped": skipped}


@app.get("/api/jobs")
def list_jobs():
    jobs = db.recent()
    for j in jobs:
        j["glossary"] = json.loads(j["glossary"]) if j.get("glossary") else []
        j["stats"] = json.loads(j["stats"]) if j.get("stats") else None
        if j["status"] in ("running", "claimed"):
            j.update(runner.live(j["id"]))
    return {"jobs": jobs}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: int):
    db.requeue(job_id)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def remove(job_id: int):
    db.delete(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/srt")
def download(job_id: int):
    job = db.get(job_id)
    if not job or not job.get("output"):
        raise HTTPException(404, "No subtitle file for this job yet")
    out = Path(job["output"])
    if not out.exists():
        raise HTTPException(404, "Subtitle file has moved or been deleted")
    return FileResponse(out, media_type="text/plain", filename=out.name)


@app.get("/api/jobs/{job_id}/cues")
def cues(job_id: int):
    job = db.get(job_id)
    if not job or not job.get("output"):
        raise HTTPException(404, "No subtitle file for this job yet")
    out = Path(job["output"])
    if not out.exists():
        raise HTTPException(404, "Subtitle file has moved or been deleted")
    return {"cues": srt.parse(out.read_text(encoding="utf-8", errors="ignore"))}


class SaveBody(BaseModel):
    cues: list[dict]
    rewrap: bool = False


@app.put("/api/jobs/{job_id}/cues")
def save_cues(job_id: int, body: SaveBody):
    job = db.get(job_id)
    if not job or not job.get("output"):
        raise HTTPException(404, "No subtitle file for this job yet")

    cleaned = []
    for i, c in enumerate(body.cues):
        try:
            start, end = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, f"Cue {i + 1} has an unreadable timecode")
        text = str(c.get("text", "")).strip()
        if not text:
            continue  # deleting a cue means clearing its text
        if end <= start:
            raise HTTPException(400, f"Cue {i + 1} ends before it starts")
        if rewrapped := (segment.wrap(text.replace("\n", " ")) if body.rewrap else None):
            text = "\n".join(rewrapped)
        cleaned.append({"start": start, "end": end, "text": text})

    if not cleaned:
        raise HTTPException(400, "That would leave the file empty")

    # An editor can easily produce out-of-order or touching cues. Fix both
    # here rather than trusting the client, since this file gets written
    # straight into the media folder.
    cleaned.sort(key=lambda c: c["start"])
    for a, b in zip(cleaned, cleaned[1:]):
        if a["end"] > b["start"] - 0.04:
            a["end"] = max(a["start"] + 0.02, b["start"] - 0.04)

    srt.write(cleaned, Path(job["output"]))
    return {"ok": True, "cues": len(cleaned)}


# -------------------------------------------------------------- live feed

@app.get("/api/stream")
async def stream(request: Request):
    """One SSE stream for the whole queue. Polls in-memory state rather than
    plumbing events across the thread boundary — far fewer moving parts."""
    async def gen():
        last = None
        while True:
            if await request.is_disconnected():
                break
            payload = list_jobs()
            body = json.dumps(payload, default=str)
            if body != last:
                last = body
                yield f"data: {body}\n\n"
            else:
                yield ": ping\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


# ---------------------------------------------------------------- webhook

@app.post("/api/webhook/sonarr")
async def sonarr(request: Request, token: str | None = None):
    """Point Sonarr's 'On Import' connection here to caption new episodes
    automatically. Sonarr sends episodeFile.path on Download events."""
    if config.SONARR_TOKEN and token != config.SONARR_TOKEN:
        raise HTTPException(401, "Bad or missing token")

    body = await request.json()
    if body.get("eventType") not in ("Download", "Test"):
        return {"ignored": body.get("eventType")}
    if body.get("eventType") == "Test":
        return {"ok": True}

    series = body.get("series") or {}
    allowed, kind = config.sonarr_series_allowed(series)
    if not allowed:
        # Logged rather than dropped quietly: "the webhook stopped working"
        # and "the webhook is filtering correctly" look identical otherwise.
        log.info("sonarr webhook: skipping %r (series type %r, accepting %s)",
                 series.get("title", "?"), kind or "unset",
                 config.SONARR_SERIES_TYPES)
        return {"skipped": series.get("title"), "series_type": kind}

    created, unreachable = [], []
    for ep in body.get("episodeFiles") or [body.get("episodeFile") or {}]:
        raw = ep.get("path")
        if not raw:
            continue
        # Sonarr reports the path as Sonarr sees it, which is only our path
        # when both containers mount the library identically.
        p = Path(config.remap_sonarr_path(raw))
        if not p.is_file():
            # The single most common webhook failure, and previously silent:
            # the POST succeeded, nothing was queued, and nothing said why.
            unreachable.append({"sonarr": raw, "looked_for": str(p)})
            continue
        if not db.has_active(str(p)):
            created.append(db.enqueue(p, p.stem))

    for miss in unreachable:
        log.warning("sonarr webhook: %s is not a file here (Sonarr said %s) — "
                    "set a path translation in Settings",
                    miss["looked_for"], miss["sonarr"])
    log.info("sonarr webhook queued %d job(s)", len(created))
    return {"created": created, "unreachable": unreachable}


# ------------------------------------------------------------------ settings

class SettingsBody(BaseModel):
    values: dict


@app.get("/api/settings")
def get_settings():
    return {"schema": settings.schema(), "values": settings.current()}


@app.put("/api/settings")
def put_settings(body: SettingsBody):
    try:
        applied = settings.save(body.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "applied": list(applied), "values": settings.current()}


@app.post("/api/settings/test-polish")
def test_polish():
    """Live check against whichever provider is selected.

    Model IDs get retired regularly and a stale one fails with a 404 only
    once a job reaches the polish stage, minutes in. This surfaces it in a
    couple of seconds instead.
    """
    ok, detail = providers.test()
    return {"ok": ok, "detail": detail, "provider": config.POLISH_PROVIDER}


@app.get("/api/settings/models")
def list_models():
    """Which models this key can actually call — beats guessing."""
    try:
        return {"models": providers.models(), "provider": config.POLISH_PROVIDER}
    except providers.ProviderError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": config.MODEL_SIZE,
        "device": config.DEVICE,
        "concurrency": config.CONCURRENCY,
        "polish": {
            "enabled": config.POLISH_ENABLED,
            "provider": config.POLISH_PROVIDER,
            "model": config.polish_model(),
        },
        # The editor validates against these rather than hardcoding its own
        # copy, so changing .env changes both sides at once.
        "rules": {
            "max_chars_per_line": config.MAX_CHARS_PER_LINE,
            "max_lines": config.MAX_LINES,
            "max_cps": config.MAX_CPS,
            "min_cue_dur": config.MIN_CUE_DUR,
            "max_cue_dur": config.MAX_CUE_DUR,
        },
    }


# ----------------------------------------------------------------- static

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
