import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("verbatim.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  path        TEXT NOT NULL,
  title       TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued',
  stage       TEXT,
  progress    REAL DEFAULT 0,
  duration    REAL DEFAULT 0,
  audio_note  TEXT,
  glossary    TEXT,
  stats       TEXT,
  output      TEXT,
  error       TEXT,
  created_at  REAL NOT NULL,
  started_at  REAL,
  ended_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def conn() -> sqlite3.Connection:
    if not hasattr(_local, "c"):
        c = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        # NOT WAL. WAL needs a -shm shared-memory file with working mmap
        # semantics, which Unraid's /mnt/user FUSE layer does not reliably
        # provide. The failure mode is nasty: the WAL grows without ever
        # checkpointing, then a later startup blocks forever in
        # uninterruptible I/O during WAL recovery, before anything has
        # logged. TRUNCATE keeps the rollback journal in one file and is
        # more than fast enough here: this is a single-writer workload
        # with a handful of writes per job.
        c.execute("PRAGMA journal_mode=TRUNCATE")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=10000")
        _local.c = c
    return _local.c


def _clear_stale_wal():
    """Remove leftover WAL files from an older build that used WAL mode.

    Opening a database with an orphaned -wal present triggers recovery, and
    that is exactly what hangs on FUSE. Deleting them costs at most the
    uncheckpointed tail of job history, which is not worth a hung container.
    """
    for suffix in ("-wal", "-shm"):
        stale = Path(str(config.DB_PATH) + suffix)
        if stale.exists():
            try:
                stale.unlink()
                log.warning("removed stale %s from a previous WAL-mode build", stale.name)
            except OSError as exc:
                log.error("could not remove %s: %s", stale.name, exc)


def init():
    log.info("opening database at %s", config.DB_PATH)
    _clear_stale_wal()
    conn().executescript(SCHEMA)
    conn().executescript(SCAN_SCHEMA)
    # Anything mid-flight when the container stopped is not coming back.
    conn().execute(
        "UPDATE jobs SET status='failed', error='Interrupted by restart' "
        "WHERE status IN ('running','claimed')"
    )
    conn().commit()
    log.info("database ready (journal_mode=TRUNCATE)")


def enqueue(path: Path, title: str) -> int:
    cur = conn().execute(
        "INSERT INTO jobs (path, title, created_at) VALUES (?,?,?)",
        (str(path), title, time.time()),
    )
    conn().commit()
    return cur.lastrowid


def claim() -> dict | None:
    """Atomically take the oldest queued job."""
    c = conn()
    with c:
        row = c.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE jobs SET status='claimed', started_at=? WHERE id=? AND status='queued'",
            (time.time(), row["id"]),
        )
    return dict(row)


def update(job_id: int, **fields):
    if not fields:
        return
    for key in ("glossary", "stats"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    sets = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
    conn().commit()


def get(job_id: int) -> dict | None:
    row = conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def recent(limit: int = 100) -> list[dict]:
    rows = conn().execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def has_active(path: str) -> bool:
    row = conn().execute(
        "SELECT 1 FROM jobs WHERE path=? AND status IN ('queued','claimed','running')",
        (path,),
    ).fetchone()
    return row is not None


def delete(job_id: int):
    conn().execute("DELETE FROM jobs WHERE id=? AND status NOT IN ('claimed','running')", (job_id,))
    conn().commit()


def requeue(job_id: int):
    conn().execute(
        "UPDATE jobs SET status='queued', progress=0, stage=NULL, error=NULL "
        "WHERE id=? AND status IN ('failed','done')",
        (job_id,),
    )
    conn().commit()


SCAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_cache (
  path      TEXT PRIMARY KEY,
  mtime     REAL NOT NULL,
  size      INTEGER NOT NULL,
  result    TEXT NOT NULL,
  scanned_at REAL NOT NULL
);
"""


def scan_cache_get(path: str, mtime: float, size: int) -> dict | None:
    """Cached ffprobe result, invalidated if the file changed on disk.

    Probing is the slow part of a batch pre-flight: a few hundred
    milliseconds per file over a network share, times a whole library. Keying
    on mtime and size means a re-scan of an unchanged folder is instant.
    """
    row = conn().execute(
        "SELECT result FROM scan_cache WHERE path=? AND mtime=? AND size=?",
        (path, mtime, size),
    ).fetchone()
    return json.loads(row["result"]) if row else None


def scan_cache_put(path: str, mtime: float, size: int, result: dict):
    conn().execute(
        "INSERT OR REPLACE INTO scan_cache (path, mtime, size, result, scanned_at) "
        "VALUES (?,?,?,?,?)",
        (path, mtime, size, json.dumps(result), time.time()),
    )
    conn().commit()
