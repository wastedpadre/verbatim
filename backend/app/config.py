import os
from pathlib import Path

# Where your anime lives. Mount your Unraid share here.
MEDIA_ROOTS = [Path(p.strip()) for p in os.getenv("MEDIA_ROOTS", "/media").split(",") if p.strip()]

# Persistent app state (SQLite db, model cache, temp audio).
DATA_DIR = Path(os.getenv("DATA_DIR", "/config"))
DB_PATH = DATA_DIR / "verbatim.db"
WORK_DIR = DATA_DIR / "work"
MODEL_DIR = DATA_DIR / "models"

# faster-whisper settings
MODEL_SIZE = os.getenv("MODEL_SIZE", "large-v3")
DEVICE = os.getenv("DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", "float16")  # int8_float16 to halve VRAM
BEAM_SIZE = int(os.getenv("BEAM_SIZE", "5"))

# How many episodes to transcribe at once. Leave at 1 for a single GPU: two
# jobs on one card contend for the same VRAM and both run slower, and on an
# 8 GB card it will run out of memory outright. Only raise this if you have a
# physically separate second GPU.
CONCURRENCY = int(os.getenv("CONCURRENCY", "1"))

# Subtitle output shaping
MAX_CHARS_PER_LINE = int(os.getenv("MAX_CHARS_PER_LINE", "42"))
MAX_LINES = int(os.getenv("MAX_LINES", "2"))
MAX_CPS = float(os.getenv("MAX_CPS", "20"))
MIN_CUE_DUR = float(os.getenv("MIN_CUE_DUR", "0.8"))
MAX_CUE_DUR = float(os.getenv("MAX_CUE_DUR", "7.0"))
CUE_GAP_SPLIT = float(os.getenv("CUE_GAP_SPLIT", "0.6"))

# Written next to the video, e.g. Episode.en.dubtitles.srt
OUTPUT_SUFFIX = os.getenv("OUTPUT_SUFFIX", ".en.dubtitles.srt")
OVERWRITE = os.getenv("OVERWRITE", "false").lower() == "true"

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".webm"}

# ---------------------------------------------------------------- Sonarr
# Sonarr posts the path as *Sonarr* sees it. If its library is mounted at
# /tv and ours at /media, every webhook lands on a file we can't open. This
# rewrites the prefix: "/tv:/media" turns /tv/Show/Ep.mkv into
# /media/Show/Ep.mkv. Comma-separate for several mounts; leave blank when
# both containers use the same path, which is the arrangement to prefer.
SONARR_PATH_MAP = os.getenv("SONARR_PATH_MAP", "")
# Accepting a POST that queues GPU work needs a shared secret once the port
# is reachable by anything other than Sonarr. Blank disables the check.
SONARR_TOKEN = os.getenv("SONARR_TOKEN", "")

# Restrict which Sonarr series types get captioned. Sonarr classifies every
# series as standard, daily or anime, and sends that on the webhook, so
# "anime" here captions your anime and ignores the rest of the library.
# Blank accepts everything. Comma-separate to allow several.
#
# Prefer filtering in Sonarr itself where you can -- a tag on the webhook
# connection means non-anime imports never leave Sonarr. This is the backstop
# for when that tag is missing or someone adds a series without it.
SONARR_SERIES_TYPES = os.getenv("SONARR_SERIES_TYPES", "")


def sonarr_series_allowed(series: dict) -> tuple[bool, str]:
    """Return (allowed, observed type) for one webhook's series object."""
    wanted = {t.strip().lower() for t in SONARR_SERIES_TYPES.split(",") if t.strip()}
    # Sonarr has spelled this both ways across versions; check both rather
    # than silently dropping every episode on an older install.
    got = str(series.get("seriesType") or series.get("type") or "").lower()
    if not wanted:
        return True, got
    return got in wanted, got


def remap_sonarr_path(raw: str) -> str:
    """Apply SONARR_PATH_MAP to one incoming path. First prefix wins."""
    for rule in SONARR_PATH_MAP.split(","):
        rule = rule.strip()
        if not rule or ":" not in rule:
            continue
        src, dst = rule.rsplit(":", 1)
        src, dst = src.strip().rstrip("/"), dst.strip().rstrip("/")
        if src and raw.startswith(src):
            return dst + raw[len(src):]
    return raw

for d in (DATA_DIR, WORK_DIR, MODEL_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- LLM polish
# Optional pass that fixes words the decoder heard wrong but which are
# obviously incorrect in context ("revert to" where "refer to" was said).
# Off by default: it costs money and sends cue text to a third party.
POLISH_ENABLED = os.getenv("POLISH_ENABLED", "false").lower() == "true"

# Which vendor runs the polish pass. Each keeps its own key and model, so
# switching providers doesn't leave a model ID pointing at the wrong API --
# "gemini-3.6-flash" sent to OpenAI is a 404, and that failure would only
# surface minutes into a job.
POLISH_PROVIDER = os.getenv("POLISH_PROVIDER", "gemini")
PROVIDERS = ("gemini", "openai", "anthropic")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Google retires Gemini model IDs on a fast cadence -- gemini-2.5-flash was
# already gone for new keys by mid-2026. The "-latest" alias tracks the
# current Flash model automatically, which avoids a hard 404 every few
# months. Pin an explicit ID (e.g. gemini-3.6-flash) if you'd rather control
# exactly when the underlying model changes.
POLISH_MODEL = os.getenv("POLISH_MODEL", "gemini-3.6-flash")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Claude Opus 5 is the current default. This task is constrained substitution
# rather than reasoning, so claude-haiku-4-5 costs a fraction and is usually
# enough -- use "List available models" in Settings to switch.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")


def polish_key() -> str:
    """The API key for whichever provider is selected."""
    return {
        "gemini": GEMINI_API_KEY,
        "openai": OPENAI_API_KEY,
        "anthropic": ANTHROPIC_API_KEY,
    }.get(POLISH_PROVIDER, "")


def polish_model() -> str:
    """The model ID for whichever provider is selected."""
    return {
        "gemini": POLISH_MODEL,
        "openai": OPENAI_MODEL,
        "anthropic": ANTHROPIC_MODEL,
    }.get(POLISH_PROVIDER, "")

# Cues per request, and how many are repeated into the next window so every
# cue is judged with neighbours on both sides at least once.
POLISH_WINDOW = int(os.getenv("POLISH_WINDOW", "40"))
POLISH_OVERLAP = int(os.getenv("POLISH_OVERLAP", "6"))

# A real correction changes a word or two. Anything that rewrites most of a
# cue is the model "improving" dialogue, and gets rejected.
POLISH_MIN_SIMILARITY = float(os.getenv("POLISH_MIN_SIMILARITY", "0.70"))
# A misheard-word fix almost never changes the word count: "revert"->"refer"
# and "Sakuro"->"Sakura" are both zero. Allowing 2 let a rewrite through in
# testing ("Yeah, whatever." -> "Yes, whatever you say."), so the ceiling is 1,
# which still covers splitting or joining a compound like "anyway"/"any way".
POLISH_MAX_WORD_DELTA = int(os.getenv("POLISH_MAX_WORD_DELTA", "1"))

# ------------------------------------------------------------------ VAD
# Voice activity detection gates audio BEFORE the model sees it, so anything
# it misclassifies as non-speech is lost permanently — no later stage can
# recover it. On dub audio with heavy scoring that's the main cause of
# missing dialogue, so these are exposed for tuning without a rebuild.
#
# VAD_FILTER=false disables gating entirely and lets the model decide. Worth
# A/B testing: with the hallucination filters and centre-channel extraction
# in place, VAD may no longer be earning its keep.
VAD_FILTER = os.getenv("VAD_FILTER", "true").lower() == "true"

# Lower = more permissive (more audio treated as speech).
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.30"))
VAD_MIN_SPEECH_MS = int(os.getenv("VAD_MIN_SPEECH_MS", "100"))
# Long values merge a pause into surrounding speech and clip the next line.
VAD_MIN_SILENCE_MS = int(os.getenv("VAD_MIN_SILENCE_MS", "400"))
# Padding around detected speech, so word onsets aren't cut off.
VAD_SPEECH_PAD_MS = int(os.getenv("VAD_SPEECH_PAD_MS", "400"))

# Windows are independent, so they're sent concurrently. This is the single
# biggest factor in how long the polish stage takes -- 20+ sequential calls
# against a thinking model is minutes of pure waiting.
POLISH_CONCURRENCY = int(os.getenv("POLISH_CONCURRENCY", "6"))

# Gemini 3.x uses thinkingLevel; 2.5 uses thinkingBudget; sending both is a
# 400. Gemini 3 Flash cannot disable thinking entirely -- "minimal" is the
# floor. This task is constrained substitution, not reasoning, so minimal is
# what we want.
POLISH_THINKING = os.getenv("POLISH_THINKING", "minimal")
