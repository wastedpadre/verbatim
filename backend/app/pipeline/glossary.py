"""Build a name/terminology glossary for the episode.

The insight this whole app hangs on: dual-audio releases ship an English
*sub* track. The dub script rewrites the dialogue heavily so we can't use its
wording, but every character name, place, technique and invented term in the
episode is sitting in there, already spelled correctly.

We extract only that vocabulary: a bare list of proper nouns. It steers the
decoder toward the right spellings and gives us something to correct against
afterward. No dialogue is carried over.
"""
import re
from collections import Counter
from pathlib import Path

TAG_RE = re.compile(r"\{[^}]*\}|<[^>]+>")          # ASS override blocks, HTML
TIMECODE_RE = re.compile(r"^\d+$|^[\d:,.\s>-]+$")
WORD_RE = re.compile(r"\b[A-Z][a-zA-Z'\-]{2,}\b")

# Capitalised words that carry no proper-noun information.
STOPLIST = {
    "The", "This", "That", "These", "Those", "There", "Their", "Then", "They",
    "What", "When", "Where", "Which", "While", "Who", "Whose", "Why", "With",
    "You", "Your", "Yes", "Well", "Not", "Now", "Nothing", "But", "And", "For",
    "From", "Have", "Has", "How", "Just", "Let", "Look", "Its", "It's", "I'm",
    "I've", "I'll", "Don't", "Can't", "Won't", "Didn't", "Was", "Were", "Will",
    "Would", "Should", "Could", "All", "Are", "Because", "Been", "Before",
    "Being", "Come", "Did", "Does", "Doing", "Done", "Even", "Ever", "Every",
    "Get", "Got", "Going", "Good", "Great", "Here", "Hey", "His", "Her", "Him",
    "Something", "Someone", "Stop", "Still", "Such", "Sure", "Take", "Tell",
    "Than", "Thank", "Thanks", "Think", "Too", "Very", "Wait", "Want", "Way",
    "Only", "Our", "Out", "Over", "Please", "Right", "Said", "Say", "See",
    "She", "Him", "Let's", "Okay", "Yeah", "Oh", "Ah", "Huh", "Hmm", "One",
    "Two", "Three", "Never", "Nobody", "Anyone", "Anything", "Everyone",
    "Everything", "Maybe", "More", "Most", "Much", "Must", "My", "Me",
}


def _strip(line: str) -> str:
    return TAG_RE.sub("", line).replace("\\N", " ").replace("\\n", " ").strip()


def from_srt(path: Path) -> list[str]:
    """Return proper nouns worth teaching the decoder, most frequent first."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    counts: Counter[str] = Counter()
    for line in raw.splitlines():
        line = _strip(line)
        if not line or TIMECODE_RE.match(line):
            continue
        # Drop the first word of each sentence: capitalisation there is
        # grammatical, not a signal that it's a name.
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            words = sentence.split()
            for w in words[1:]:
                for m in WORD_RE.findall(w):
                    if m not in STOPLIST:
                        counts[m] += 1

    # A real name recurs. A one-off capitalised word is usually noise.
    terms = [w for w, n in counts.most_common(120) if n >= 2]
    return terms


def merge(*sources: list[str]) -> list[str]:
    seen, out = set(), []
    for src in sources:
        for term in src:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                out.append(term)
    return out


def to_prompt(terms: list[str], limit: int = 60) -> str:
    """faster-whisper's initial_prompt is capped at 224 tokens, so keep it
    tight and prefer the most frequent terms."""
    if not terms:
        return ""
    picked = terms[:limit]
    return "Names and terms used in this episode: " + ", ".join(picked) + "."
