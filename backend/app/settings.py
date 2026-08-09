"""Runtime-editable settings.

Environment variables set the defaults. Anything changed in the UI is written
to /config/settings.json and layered on top at startup, so it survives a
container recreate — which matters, because `--env-file` is only read when a
container is created, making every toggle a rebuild-and-recreate cycle.

Only settings that are genuinely re-read per job are exposed. Things like
MODEL_SIZE or DEVICE are bound when the model loads, so changing them at
runtime would silently do nothing until a restart; those stay env-only rather
than pretending to work.
"""
import json
import logging
import threading
from pathlib import Path

from . import config

log = logging.getLogger("verbatim.settings")

STORE = config.DATA_DIR / "settings.json"
_lock = threading.Lock()

# name -> (type, group, label, hint)
EDITABLE = {
    "POLISH_ENABLED": (bool, "polish", "Repair misheard words",
                       "Sends cue text to Google. Roughly 2 cents an episode."),
    "GEMINI_API_KEY": (str, "polish", "Gemini API key",
                       "From aistudio.google.com. Stored on your server."),
    "POLISH_MODEL": (str, "polish", "Model",
                     "Google retires model IDs often. Use Test connection after changing."),
    "POLISH_CONCURRENCY": (int, "polish", "Parallel requests",
                           "Lower this if you hit rate limits."),
    "POLISH_THINKING": (str, "polish", "Thinking level",
                        "minimal for Gemini 3.x. More reasoning is latency you don't need here."),
    "POLISH_MIN_SIMILARITY": (float, "polish", "Minimum similarity",
                              "Reject a correction that rewrites more than this much of a line."),
    "POLISH_MAX_WORD_DELTA": (int, "polish", "Max word count change",
                              "1 is safe. Higher lets rephrasing through."),

    "VAD_FILTER": (bool, "audio", "Gate non-speech audio",
                   "Off lets the model hallucinate over music. Leave on."),
    "VAD_THRESHOLD": (float, "audio", "Speech sensitivity",
                      "Lower recovers quiet dialogue under scoring. 0.15 measured best."),
    "BEAM_SIZE": (int, "audio", "Beam size",
                  "Higher is marginally more accurate and slower."),

    "MAX_CHARS_PER_LINE": (int, "captions", "Characters per line",
                           "Lower to about 37 if you mostly watch on a phone."),
    "MAX_LINES": (int, "captions", "Lines per cue", "Two is standard for broadcast."),
    "MAX_CPS": (float, "captions", "Reading speed limit",
                "Characters per second. Lower if captions feel rushed."),
    "OUTPUT_SUFFIX": (str, "captions", "Output filename suffix",
                      "Changing this orphans existing files and re-queues them as uncaptioned."),
    "OVERWRITE": (bool, "captions", "Overwrite existing subtitles",
                  "Off appends a timestamp instead of replacing."),
}

SECRETS = {"GEMINI_API_KEY"}

GROUPS = [
    ("polish", "Word repair", "Fixes words the decoder misheard but which are wrong in context."),
    ("audio", "Audio and transcription", "How much audio reaches the model."),
    ("captions", "Caption formatting", "Shape and naming of the output."),
]


def _coerce(name: str, raw):
    kind = EDITABLE[name][0]
    if kind is bool:
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("true", "1", "yes", "on")
    if kind is int:
        return int(raw)
    if kind is float:
        return float(raw)
    return str(raw)


def load():
    """Apply saved overrides over the env-derived defaults."""
    if not STORE.exists():
        return
    try:
        saved = json.loads(STORE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("settings: could not read %s (%s), using env defaults", STORE, exc)
        return

    for name, value in saved.items():
        if name not in EDITABLE:
            continue
        try:
            setattr(config, name, _coerce(name, value))
        except (TypeError, ValueError):
            log.warning("settings: ignoring bad value for %s", name)
    log.info("settings: applied %d saved override(s)", len(saved))


def current(reveal_secrets: bool = False) -> dict:
    out = {}
    for name in EDITABLE:
        value = getattr(config, name, None)
        if name in SECRETS and not reveal_secrets:
            value = f"{'•' * 8}{str(value)[-4:]}" if value else ""
        out[name] = value
    return out


def save(updates: dict) -> dict:
    """Validate, apply live, and persist. Returns the applied subset."""
    applied = {}
    with _lock:
        for name, raw in updates.items():
            if name not in EDITABLE:
                continue
            # A masked value means the field wasn't edited — don't overwrite
            # a real secret with its own placeholder.
            if name in SECRETS and isinstance(raw, str) and raw.startswith("•"):
                continue
            try:
                value = _coerce(name, raw)
            except (TypeError, ValueError):
                raise ValueError(f"{EDITABLE[name][2]}: '{raw}' is not valid")
            setattr(config, name, value)
            applied[name] = value

        existing = {}
        if STORE.exists():
            try:
                existing = json.loads(STORE.read_text())
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing.update(applied)
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(existing, indent=2))

    log.info("settings: saved %s", ", ".join(applied) or "nothing")
    return applied


def schema() -> list[dict]:
    """Field metadata for the UI, so labels and hints live in one place."""
    out = []
    for key, title, blurb in GROUPS:
        fields = [
            {"name": n, "type": t.__name__, "label": lbl, "hint": hint,
             "secret": n in SECRETS}
            for n, (t, g, lbl, hint) in EDITABLE.items() if g == key
        ]
        out.append({"key": key, "title": title, "blurb": blurb, "fields": fields})
    return out
