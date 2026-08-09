"""Keep the last N log lines in memory so the UI can show them.

Everything interesting already goes to the logger: which track was picked,
why a Sonarr path didn't resolve, which polish windows failed, but reading
it means `docker logs`, and on Unraid that's a terminal most people won't
open. This makes the same stream visible in the browser.

Memory only, capped, and never written to disk: /config is on a cache pool
and a log file that grows without bound there is a worse problem than the
one this solves.
"""
import logging
import threading
from collections import deque

# ~1500 lines is a few full jobs, enough to cover "what happened on the
# episode before last" without holding a session's entire history.
CAPACITY = 1500

_lock = threading.Lock()
_lines: deque = deque(maxlen=CAPACITY)
# Monotonic across the process lifetime, so a client that reconnects can ask
# for "everything after 412" and get exactly that. Deque indices can't do
# this: they shift every time an old line is evicted.
_seq = 0


class BufferHandler(logging.Handler):
    """Appends every formatted record to the ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001 - a broken format must not kill logging
            return
        with _lock:
            _seq += 1
            _lines.append({
                "seq": _seq,
                "level": record.levelname,
                "text": text,
            })


def install(fmt: str) -> None:
    """Attach the buffer to the root logger, matching the console format."""
    handler = BufferHandler()
    handler.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(handler)


def since(cursor: int) -> tuple[list[dict], int]:
    """Return (lines newer than cursor, new cursor).

    A cursor of 0 means "everything buffered", which is what a freshly
    opened log panel wants; an empty box until the next line arrives would
    look broken on an idle server.
    """
    with _lock:
        out = [ln for ln in _lines if ln["seq"] > cursor]
        return out, (out[-1]["seq"] if out else cursor)


def head() -> int:
    with _lock:
        return _seq
