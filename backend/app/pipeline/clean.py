"""Post-decode cleanup.

Three problems, in order of how much they hurt: hallucinated filler over
music, repetition loops, and mangled proper nouns.
"""
import difflib
import re

# Phrases the model emits over non-speech audio. It learned them from
# subtitle files scraped off the internet, so they surface as confident,
# well-formed lines that happen to have nothing to do with the audio.
HALLUCINATION_PATTERNS = [
    r"^thanks? (you )?for watching",
    r"^please subscribe",
    r"^subscribe to",
    r"^like and subscribe",
    r"^see you (in the )?next (time|video|episode)",
    r"^subtitles? by",
    r"^transcri(bed|ption) by",
    r"^translated by",
    r"^\[?(music|applause|silence|blank_audio|inaudible)\]?$",
    r"^amara\.org",
    r"^www\.",
    r"^copyright",
    r"^end of (the )?(video|episode)",
]
HALLUCINATION_RE = [re.compile(p, re.I) for p in HALLUCINATION_PATTERNS]

COMMON_WORDS = set("""
a about after all also am an and any are as at back be because been before
being but by call can come could day did do does doing done down each even
every first for from get give go going good got great had has have he her
here him his how i if in into is it its just know let like little long look
made make man many may me might more most much must my never new no not now
of off on once one only or other our out over own said same say see she
should since so some still such take than that the their them then there
these they thing think this those through time to too two up us use very
want was way we well were what when where which while who why will with
would year you your
""".split())


def _mostly_non_latin(text: str, limit: float = 0.30) -> bool:
    """True when a segment is largely outside Latin script.

    Codepoints above U+024F are past the end of Latin Extended-B, so this
    catches CJK, Cyrillic, Arabic, Devanagari and the rest while leaving
    accented Latin (café, naïve) and all punctuation alone.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_latin = sum(1 for c in letters if ord(c) > 0x024F)
    return non_latin / len(letters) > limit


def _looks_hallucinated(seg: dict) -> bool:
    """Decide whether a segment is model filler rather than real dialogue.

    The bar here has to be high. An earlier version dropped any segment under
    60 characters whose no_speech_prob was above 0.75, which silently deleted
    huge amounts of genuine dialogue: dub scripts are full of short lines
    ("Wait." / "I understand." / "What are you doing?"), and a quiet line
    delivered under loud scoring scores badly on exactly those confidence
    signals. Missing dialogue is a far worse failure than an occasional
    spurious line, so confidence alone is no longer grounds for deletion.
    """
    text = seg["text"].strip()
    if not text:
        return True

    low = text.lower().strip(" .!?-")

    # Known filler the model emits over non-speech. Safe to drop on sight
    # because these phrases don't occur in dub dialogue.
    if any(r.search(low) for r in HALLUCINATION_RE):
        return True

    # Script drift. The audio is an English dub, so a segment made largely of
    # CJK, Cyrillic, Arabic or similar isn't a mistranscription — it's the
    # decoder inventing output for non-speech audio. This happens even with
    # the language pinned to English, and it's most visible when VAD is
    # relaxed enough to pass music and silence through.
    if _mostly_non_latin(text):
        return True

    # Degenerate output compresses unusually well. This catches genuine
    # repetition loops rather than merely uncertain transcription.
    if seg.get("compression_ratio", 0) > 2.8:
        return True

    # Last resort: drop only when every signal agrees it's junk AND the text
    # is too short to be a meaningful line. All three must hold.
    if (seg.get("no_speech_prob", 0) > 0.9
            and seg.get("avg_logprob", 0) < -1.0
            and len(text) < 12):
        return True

    return False


def _key(word: str) -> str:
    return re.sub(r"[^\w']", "", word).lower()


def _strip_repeat_words(words: list[dict], cap: int = 3) -> tuple[list[dict], bool]:
    """Collapse runs of the same word, and phrase-level loops, on the word
    array itself.

    This has to operate on words rather than on the joined text, because the
    cue builder reads timings straight off the word list. Fixing only the
    text string would leave the repeats in the timings and they'd reappear
    downstream.
    """
    changed = False

    # Word-level: 'no no no no no no' -> 'no no no'
    out, run = [], 1
    for w in words:
        if out and _key(w["word"]) and _key(w["word"]) == _key(out[-1]["word"]):
            run += 1
            if run > cap:
                # Fold the dropped word's duration into the kept one so the
                # cue doesn't end early.
                out[-1]["end"] = w["end"]
                changed = True
                continue
        else:
            run = 1
        out.append(w)

    # Phrase-level: an n-word phrase repeated 3+ times back to back.
    for n in range(5, 1, -1):
        i = 0
        result = []
        while i < len(out):
            phrase = [_key(x["word"]) for x in out[i:i + n]]
            if len(phrase) < n or not all(phrase):
                result.append(out[i]); i += 1
                continue
            reps = 1
            j = i + n
            while [_key(x["word"]) for x in out[j:j + n]] == phrase:
                reps += 1
                j += n
            if reps >= 3:
                block = out[i:i + n]
                block[-1]["end"] = out[j - 1]["end"]
                result.extend(block)
                changed = True
                i = j
            else:
                result.append(out[i]); i += 1
        out = result

    return out, changed


def _text_of(words: list[dict]) -> str:
    return re.sub(r"\s{2,}", " ", "".join(w["word"] for w in words)).strip()


def dedupe_segments(segments: list[dict]) -> list[dict]:
    out = []
    for seg in segments:
        if out and seg["text"].strip().lower() == out[-1]["text"].strip().lower():
            # Same line twice in a row: extend the previous cue instead.
            out[-1]["end"] = seg["end"]
            out[-1]["words"].extend(seg["words"])
            continue
        out.append(seg)
    return out


def correct_terms(word: str, terms: list[str], threshold: float = 0.82) -> str:
    """Nudge a decoded word toward a known name if it's clearly a near-miss."""
    bare = re.sub(r"[^\w'\-]", "", word)
    if not bare or bare.lower() in COMMON_WORDS or len(bare) < 4:
        return word
    if any(bare.lower() == t.lower() for t in terms):
        return word

    match = difflib.get_close_matches(bare, terms, n=1, cutoff=threshold)
    if not match:
        return word
    replacement = match[0]
    # Don't "correct" a perfectly ordinary English word into a character name.
    if bare.lower() in COMMON_WORDS:
        return word
    return word.replace(bare, replacement)


def apply(segments: list[dict], terms: list[str]) -> tuple[list[dict], dict]:
    stats = {"dropped": 0, "repaired": 0, "renamed": 0}
    kept = []

    for seg in segments:
        if _looks_hallucinated(seg):
            stats["dropped"] += 1
            continue

        # Order matters: repetition first (it removes words), then name
        # correction (it rewrites the words that survive), then derive the
        # text once at the end from whatever is left.
        seg["words"], repaired = _strip_repeat_words(seg["words"])
        if repaired:
            stats["repaired"] += 1

        if terms:
            for w in seg["words"]:
                fixed = correct_terms(w["word"], terms)
                if fixed != w["word"]:
                    stats["renamed"] += 1
                    w["word"] = fixed

        seg["text"] = _text_of(seg["words"])
        if not seg["text"]:
            stats["dropped"] += 1
            continue
        seg["end"] = seg["words"][-1]["end"]

        kept.append(seg)

    return dedupe_segments(kept), stats
