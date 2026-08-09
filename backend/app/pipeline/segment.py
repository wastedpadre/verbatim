"""Turn word-level timings into cues a human can actually read.

The model segments for transcription accuracy, which produces cues that are
too long, too fast, or broken in the middle of a clause. Broadcast captioning
has well-established rules; this applies them.
"""
import re

from .. import config

SENTENCE_END = re.compile(r"[.!?…]['\"\)\]]?$")
CLAUSE_END = re.compile(r"[,;:—–]['\"\)\]]?$")


def _cps(text: str, start: float, end: float) -> float:
    dur = max(end - start, 0.01)
    return len(text) / dur


def wrap(text: str) -> list[str]:
    """Greedy word wrap at the line limit. Used both for output and, more
    importantly, to decide in advance whether a cue will fit."""
    limit = config.MAX_CHARS_PER_LINE
    lines, cur = [], ""
    for word in text.split():
        candidate = f"{cur} {word}".strip()
        if cur and len(candidate) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines or [""]


def fits(text: str) -> bool:
    return len(wrap(text)) <= config.MAX_LINES


def _split_lines(text: str) -> str:
    """Balance a cue across at most MAX_LINES lines, breaking at the most
    natural point rather than at the character limit."""
    limit = config.MAX_CHARS_PER_LINE
    if len(text) <= limit:
        return text

    words = text.split()
    if config.MAX_LINES < 2:
        return text

    # Prefer a break that leaves both lines close in length, and bias toward
    # breaking after punctuation rather than mid-clause.
    best, best_cost = None, float("inf")
    for i in range(1, len(words)):
        top = " ".join(words[:i])
        bottom = " ".join(words[i:])
        if len(top) > limit or len(bottom) > limit:
            continue
        cost = abs(len(top) - len(bottom))
        if CLAUSE_END.search(top):
            cost -= 12
        # Never strand an article or preposition at the end of a line.
        if words[i - 1].lower() in {"a", "an", "the", "and", "or", "of", "to", "in", "on"}:
            cost += 20
        if cost < best_cost:
            best, best_cost = (top, bottom), cost

    if best is None:
        # No balanced two-line split exists, usually long unbroken words.
        # Fall back to a greedy wrap rather than emitting an overlong line.
        return "\n".join(wrap(text))
    return best[0] + "\n" + best[1]


def build_cues(segments: list[dict]) -> list[dict]:
    # Carry the source segment index along. A segment boundary is a real
    # signal: the decoder put it there, so we let it end a cue rather than
    # running two utterances together.
    words = []
    for si, seg in enumerate(segments):
        for w in seg["words"]:
            if w["word"].strip():
                words.append({**w, "_seg": si})
    if not words:
        return []

    cues, buf = [], []

    def flush():
        if not buf:
            return
        text = "".join(w["word"] for w in buf).strip()
        text = re.sub(r"\s{2,}", " ", text)
        if not text:
            buf.clear()
            return
        start, end = buf[0]["start"], buf[-1]["end"]

        # A cue that flashes past faster than the eye can track gets held
        # longer, as long as it doesn't collide with what follows.
        if _cps(text, start, end) > config.MAX_CPS:
            end = start + len(text) / config.MAX_CPS
        if end - start < config.MIN_CUE_DUR:
            end = start + config.MIN_CUE_DUR

        cues.append({"start": start, "end": end, "text": _split_lines(text)})
        buf.clear()

    for i, w in enumerate(words):
        buf.append(w)
        text_so_far = "".join(x["word"] for x in buf).strip()
        nxt = words[i + 1] if i + 1 < len(words) else None

        gap = (nxt["start"] - w["end"]) if nxt else 999
        dur = w["end"] - buf[0]["start"]
        crosses_segment = nxt is not None and nxt["_seg"] != w["_seg"]
        ends_sentence = bool(SENTENCE_END.search(text_so_far))

        should_break = (
            nxt is None
            or gap >= config.CUE_GAP_SPLIT               # a real pause in delivery
            or dur >= config.MAX_CUE_DUR
            # Test the *wrapped* result, not the character count. A cue can be
            # under the raw budget and still need three lines if the words are
            # long, which is how overlong lines sneak through.
            or (nxt is not None and not fits(text_so_far + " " + nxt["word"].strip()))
            # Short exclamations are constant in dub dialogue ("Get out!",
            # "Look out!"), so the length gate here stays low. Anything above
            # it that closes a sentence gets its own cue.
            or (ends_sentence and len(text_so_far) >= 8)
            # The decoder chose to end a segment here. If the text also reads
            # as complete, don't run it into the next utterance.
            or (crosses_segment and ends_sentence)
        )
        if should_break:
            flush()

    flush()

    # Two cues can end up starting at effectively the same instant when the
    # decoder emits zero-width words. Merge those before timing cleanup.
    merged = []
    for c in cues:
        if merged and c["start"] - merged[-1]["start"] < 0.08:
            joined = merged[-1]["text"].replace("\n", " ") + " " + c["text"].replace("\n", " ")
            merged[-1]["text"] = _split_lines(re.sub(r"\s{2,}", " ", joined))
            merged[-1]["end"] = max(merged[-1]["end"], c["end"])
        else:
            merged.append(c)
    cues = merged

    # Stop consecutive cues from overlapping after the CPS stretch above.
    # Never letting a cue run into the next one takes priority over holding
    # it for the minimum duration: an overlap is a visible rendering bug,
    # a slightly short cue is not.
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"] - 0.04:
            a["end"] = max(a["start"] + 0.02, b["start"] - 0.04)

    return cues


def shift(cues: list[dict], offset: float) -> list[dict]:
    if not offset:
        return cues
    for c in cues:
        c["start"] += offset
        c["end"] += offset
    return cues
