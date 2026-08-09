"""Work out which streams inside a container we actually care about.

Dual-audio anime releases are the whole reason this module exists. A typical
release has two audio tracks (Japanese + English) and two or three subtitle
tracks (full English translation, signs & songs, sometimes a commentary).
Grabbing the wrong one silently produces garbage, so be explicit.
"""
import json
import subprocess
from pathlib import Path

ENG = {"eng", "en", "english"}

# Release groups are inconsistent about language tags but fairly consistent
# about titles, so titles are a usable fallback signal.
DUB_HINTS = ("english", "dub", "eng")
JPN_HINTS = ("japanese", "jpn", "jp")
SIGNS_HINTS = ("sign", "song", "forced", "s&s")


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def _tags(stream: dict) -> tuple[str, str]:
    tags = stream.get("tags") or {}
    lang = (tags.get("language") or tags.get("LANGUAGE") or "").lower()
    title = (tags.get("title") or tags.get("TITLE") or "").lower()
    return lang, title


def duration(info: dict) -> float:
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return 0.0


def pick_audio(info: dict) -> tuple[int | None, str, int]:
    """Return (absolute stream index, human explanation, channel count)."""
    audio = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    if not audio:
        return None, "no audio streams", 0

    scored = []
    for s in audio:
        lang, title = _tags(s)
        score = 0
        if lang in ENG:
            score += 100
        if any(h in title for h in DUB_HINTS):
            score += 40
        if lang in JPN_HINTS or any(h in title for h in JPN_HINTS):
            score -= 100
        if s.get("disposition", {}).get("default"):
            score += 5
        # Dubs are frequently the higher-channel-count mix on BD releases.
        score += min(int(s.get("channels") or 0), 8)
        scored.append((score, s))

    scored.sort(key=lambda x: -x[0])
    best_score, best = scored[0]
    lang, title = _tags(best)
    channels = int(best.get("channels") or 2)

    if best_score < 40:
        why = (f"no track tagged English; falling back to stream "
               f"{best['index']} (lang={lang or 'untagged'!r})")
    else:
        why = (f"stream {best['index']} (lang={lang or 'untagged'!r}, "
               f"title={title or '-'!r}, {channels}ch)")
    return best["index"], why, channels


def pick_subtitle(info: dict) -> tuple[int | None, str | None]:
    """Find the best English text subtitle stream to mine for proper nouns.

    This is the *sub* script, not the dub script. We never use its wording,
    only its spelling of names and terminology.
    """
    subs = [s for s in info["streams"] if s.get("codec_type") == "subtitle"]
    scored = []
    for s in subs:
        codec = s.get("codec_name", "")
        if codec not in ("subrip", "ass", "ssa", "mov_text", "webvtt"):
            continue  # PGS/VobSub are bitmaps, no text to mine
        lang, title = _tags(s)
        score = 0
        if lang in ENG:
            score += 100
        if any(h in title for h in SIGNS_HINTS):
            score -= 60  # signs-only tracks are too sparse to be useful
        if "full" in title or "dialogue" in title:
            score += 20
        scored.append((score, s, codec))

    if not scored:
        return None, None
    scored.sort(key=lambda x: -x[0])
    score, best, codec = scored[0]
    if score < 0:
        return None, None
    return best["index"], codec


# Some releases already ship a dub-synced subtitle track. Transcribing those
# is pure waste: six minutes of GPU time to reproduce something the file
# already contains, usually less accurately than the original.
DUBTITLE_HINTS = ("dubtitle", "dubtitles", "dub sub", "dubsub", "english dub")

TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt"}


def has_embedded_dubtitle(info: dict) -> str | None:
    """Return the track title if the file already carries a dubtitle track."""
    for s in info["streams"]:
        if s.get("codec_type") != "subtitle":
            continue
        if s.get("codec_name") not in TEXT_SUB_CODECS:
            continue
        lang, title = _tags(s)
        if lang in ENG and any(h in title for h in DUBTITLE_HINTS):
            tags = s.get("tags") or {}
            return tags.get("title") or tags.get("TITLE") or "English (Dubtitle)"
    return None


def has_english_audio(info: dict) -> bool:
    """True when a track is plausibly an English dub.

    Deliberately generous: an untagged single audio track on an English-language
    show should not be skipped. The cost of a false positive is one wasted job;
    the cost of a false negative is silently never captioning a show.
    """
    audio = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    if not audio:
        return False
    for s in audio:
        lang, title = _tags(s)
        if lang in ENG:
            return True
        if any(h in title for h in DUB_HINTS):
            return True
    # Single untagged track: can't rule it out, so let it through.
    if len(audio) == 1 and not _tags(audio[0])[0]:
        return True
    return False


def classify(path) -> dict:
    """One ffprobe, everything the batch pre-flight needs to decide."""
    try:
        info = ffprobe(path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"unreadable: {exc}"}

    dub = has_embedded_dubtitle(info)
    eng = has_english_audio(info)
    idx, note, channels = pick_audio(info)

    return {
        "ok": True,
        "english_audio": eng,
        "embedded_dubtitle": dub,
        "audio_note": note,
        "channels": channels,
        "duration": duration(info),
    }
