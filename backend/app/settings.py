"""Runtime-editable settings.

Environment variables set the defaults. Anything changed in the UI is written
to /config/settings.json and layered on top at startup, so it survives a
container recreate — which matters, because `--env-file` is only read when a
container is created, making every toggle a rebuild-and-recreate cycle.

Most settings here are re-read per job, so they take effect immediately. The
few that aren't — MODEL_SIZE and COMPUTE_TYPE, which are bound when the model
loads — are listed in RESTART_REQUIRED and flagged to the UI, so it can say
"on next start" rather than implying a change that hasn't happened yet.

DEVICE stays env-only on purpose: the only reason to move off cuda is to test
on CPU, which is roughly ten times slower, and putting that one click away in
a web UI invites a very confusing bug report.
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
    "MODEL_SIZE": (str, "model", "Speech model",
                   "large-v3 is the most accurate. distil-large-v3 is about twice "
                   "as fast for a small accuracy cost; medium.en and below are "
                   "faster still and fine on clean dubs."),
    "COMPUTE_TYPE": (str, "model", "Precision",
                     "float16 uses ~4.7 GB of VRAM for large-v3 and works on every "
                     "card. int8_float16 roughly halves that on an 8 GB card — but "
                     "not on an RTX 50-series, where int8 has no cuBLAS path and "
                     "every job fails."),

    "POLISH_ENABLED": (bool, "polish", "Repair misheard words",
                       "Sends cue text to the provider below. Roughly 2 cents an episode."),
    "POLISH_PROVIDER": (str, "polish", "Provider",
                        "Which vendor runs the pass. Each keeps its own key and "
                        "model below. Gemini is the best-worn path; hit Test "
                        "connection after switching to either of the others."),
    "GEMINI_API_KEY": (str, "polish", "Gemini API key",
                       "From aistudio.google.com. Stored on your server, never sent anywhere else."),
    "POLISH_MODEL": (str, "polish", "Gemini model",
                     "Google retires model IDs often. Use Test connection after changing."),
    "OPENAI_API_KEY": (str, "polish", "OpenAI API key",
                       "From platform.openai.com/api-keys. Stored on your server."),
    "OPENAI_MODEL": (str, "polish", "OpenAI model",
                     "A mini/small model is plenty — this is substitution, not reasoning."),
    "ANTHROPIC_API_KEY": (str, "polish", "Anthropic API key",
                          "From console.anthropic.com. Stored on your server."),
    "ANTHROPIC_MODEL": (str, "polish", "Anthropic model",
                        "claude-haiku-4-5 costs a fraction of Opus and is usually enough here."),
    "POLISH_CONCURRENCY": (int, "polish", "Parallel requests",
                           "Lower this if you hit rate limits."),
    "POLISH_THINKING": (str, "polish", "Gemini thinking level",
                        "minimal for Gemini 3.x. Ignored by the other two providers."),
    "POLISH_MIN_SIMILARITY": (float, "polish", "Minimum similarity",
                              "Reject a correction that rewrites more than this much of a line."),
    "POLISH_MAX_WORD_DELTA": (int, "polish", "Max word count change",
                              "1 is safe. Higher lets rephrasing through."),

    "BEAM_SIZE": (int, "audio", "Beam size",
                  "Higher is marginally more accurate and slower."),

    "VAD_FILTER": (bool, "vad", "Gate non-speech audio",
                   "Off hands all audio to the model and lets the hallucination "
                   "filters clean up after. Worth trying if lines go missing."),
    "VAD_THRESHOLD": (float, "vad", "Speech sensitivity",
                      "0 to 1. Lower recovers quiet dialogue under scoring; 0.15 "
                      "measured best on dub audio, 0.30 lost real lines."),
    "VAD_MIN_SPEECH_MS": (int, "vad", "Shortest speech (ms)",
                          "Bursts shorter than this are discarded as noise. Raise it "
                          "if single-syllable effects are being transcribed."),
    "VAD_MIN_SILENCE_MS": (int, "vad", "Shortest silence (ms)",
                           "A gap shorter than this doesn't split speech. Long values "
                           "merge a pause into the next line and clip its opening."),
    "VAD_SPEECH_PAD_MS": (int, "vad", "Padding around speech (ms)",
                          "Kept either side of detected speech. Raise it if the first "
                          "word of lines is being cut off."),

    "MAX_CHARS_PER_LINE": (int, "captions", "Characters per line",
                           "Lower to about 37 if you mostly watch on a phone."),
    "MAX_LINES": (int, "captions", "Lines per cue", "Two is standard for broadcast."),
    "MAX_CPS": (float, "captions", "Reading speed limit",
                "Characters per second. Lower if captions feel rushed."),
    "OUTPUT_SUFFIX": (str, "captions", "Output filename suffix",
                      "Changing this orphans existing files and re-queues them as uncaptioned."),
    "OVERWRITE": (bool, "captions", "Overwrite existing subtitles",
                  "Off appends a timestamp instead of replacing."),

    "SONARR_SERIES_TYPES": (str, "sonarr", "Only these series types",
                            "Sonarr classifies every series as standard, daily or "
                            "anime. Put 'anime' here to caption only anime and "
                            "ignore the rest of the library. Blank accepts everything."),
    "SONARR_PATH_MAP": (str, "sonarr", "Path translation",
                        "Only needed when Sonarr and Verbatim mount the library at "
                        "different paths. Format: /sonarr/path:/verbatim/path"),
    "SONARR_TOKEN": (str, "sonarr", "Webhook token",
                     "Optional. When set, the URL below must carry ?token=… or the "
                     "POST is rejected."),
}

SECRETS = {"GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SONARR_TOKEN"}

# Bound when the model loads rather than read per job, so a change here is
# saved and applied at the next container start. Flagged to the UI so it can
# say that instead of the usual "applies to the next job".
RESTART_REQUIRED = {"MODEL_SIZE", "COMPUTE_TYPE"}

# Dropdown seeds for fields that must stay open-ended. Unlike CHOICES these
# are NOT validated: vendors add and retire model IDs constantly, so the list
# is a starting point merged with whatever "List available models" returns,
# and a custom ID typed in the UI still saves.
SUGGESTIONS = {
    "ANTHROPIC_MODEL": ["claude-haiku-4-5", "claude-sonnet-5",
                        "claude-opus-4-8", "claude-opus-5"],
    # Confirmed present on a real key. Availability varies by account tier,
    # which is what "Refresh model list" is for.
    "OPENAI_MODEL": ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o-mini",
                     "gpt-5-mini", "gpt-5.4-mini", "gpt-5"],
    "POLISH_MODEL": ["gemini-flash-latest", "gemini-3.6-flash",
                     "gemini-3.5-flash-lite"],
}

# Fields the UI should render as a picker rather than a free text box.
CHOICES = {
    "POLISH_PROVIDER": list(config.PROVIDERS),
    # faster-whisper accepts more than this, but these are the ones worth
    # choosing between; anything exotic can still go in .env.
    "MODEL_SIZE": ["large-v3", "distil-large-v3", "medium.en", "medium",
                   "small.en", "small", "base.en", "tiny.en"],
    "COMPUTE_TYPE": ["float16", "int8_float16", "int8", "float32"],
}

GROUPS = [
    ("model", "Speech model",
     "What gets loaded onto the GPU. Unlike everything else here, these are "
     "bound when the model loads, so a change is saved now and takes effect "
     "when the container next starts."),
    ("polish", "Word repair",
     "Fixes words the decoder misheard but which are wrong in context. "
     "Optional, off by default, and the only feature that sends anything off your server."),
    ("audio", "Audio and transcription", "How much audio reaches the model."),
    ("vad", "Voice activity detection",
     "VAD gates audio BEFORE the model sees it, so whatever it drops is gone for "
     "good. On dub audio with loud scoring this is the usual cause of missing "
     "dialogue. These apply to the next job — no restart, no rebuild."),
    ("captions", "Caption formatting", "Shape and naming of the output."),
    ("sonarr", "Sonarr webhook", "Caption new episodes automatically as they import."),
]


def _coerce(name: str, raw):
    kind = EDITABLE[name][0]
    if kind is bool:
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("true", "1", "yes", "on")
    if kind is int:
        return int(raw)
    if kind is float:
        return float(raw)
    value = str(raw)
    # A bad provider name would otherwise be stored happily and only fail at
    # the polish stage, minutes into a job.
    if name in CHOICES and value not in CHOICES[name]:
        raise ValueError(f"must be one of {', '.join(CHOICES[name])}")
    return value


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
            except (TypeError, ValueError) as exc:
                reason = str(exc) if name in CHOICES else "is not valid"
                raise ValueError(f"{EDITABLE[name][2]}: '{raw}' {reason}")
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
             "secret": n in SECRETS, "choices": CHOICES.get(n),
             "suggestions": SUGGESTIONS.get(n),
             "restart": n in RESTART_REQUIRED}
            for n, (t, g, lbl, hint) in EDITABLE.items() if g == key
        ]
        out.append({"key": key, "title": title, "blurb": blurb, "fields": fields})
    return out
