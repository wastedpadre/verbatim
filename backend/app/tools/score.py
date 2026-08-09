"""Score generated captions against a reference subtitle track.

Some anime releases ship a real dubtitle track. When one exists, it's ground
truth: it's exactly what this pipeline is trying to reproduce. Comparing
against it turns "seems better" into a number you can act on.

Extract the reference track first (stream index from ffprobe):

    ffprobe -v error -show_entries stream=index,codec_type:stream_tags=title \\
        -of csv=p=0 "/media/Show/Season 01/Episode.mkv"

    ffmpeg -v error -y -i "/media/Show/Season 01/Episode.mkv" \\
        -map 0:7 -c:s srt /config/reference.srt

Then:

    python3 -m app.tools.score /config/reference.srt "/media/.../Episode.en.dubtitles.srt"

Two numbers come back, and they fail differently:

  accuracy  how many reference words we got right
  coverage  how much of the script we produced at all

Low coverage with decent accuracy means dialogue is being dropped -- look at
the VAD settings and the filters in clean.py. Good coverage with low accuracy
means the audio is being misheard -- look at the downmix in audio.py, since
a flat 5.1 downmix buries dialogue under the score.
"""
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.pipeline import srt  # noqa: E402

WORD_RE = re.compile(r"[a-z']+")

# ASS/SSA subtitle tracks carry typesetting alongside dialogue. Converting one
# to SRT keeps all of it, and counting that markup as words inflates the
# reference wildly -- a 24-minute episode measured at 117k "words" is markup,
# not dialogue.
ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")            # {\pos(640,50)\fad(200,0)}
ASS_DRAWING_RE = re.compile(                          # vector shapes: m 0 0 l 96 0 b ...
    r"(?:^|\s)[mnlbspc](?:\s+-?\d+(?:\.\d+)?)+", re.I)
LINE_BREAK_RE = re.compile(r"\\[Nnh]")
HTML_RE = re.compile(r"<[^>]+>")

def _strip(text: str) -> str:
    """Remove typesetting, keep dialogue.

    Note there is deliberately no keyword blocklist here. An earlier version
    filtered ASS tag names ("an", "be", "blur", "fade", "clip", "move"), which
    silently deleted real English words from both sides of the comparison. The
    brace regex already removes whole override blocks, so the tag names never
    survive as bare tokens anyway -- the blocklist was redundant and harmful.
    """
    text = ASS_OVERRIDE_RE.sub(" ", text)
    text = ASS_DRAWING_RE.sub(" ", text)
    text = LINE_BREAK_RE.sub(" ", text)
    return HTML_RE.sub(" ", text)


def words(path: str) -> list[str]:
    cues = srt.parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
    return WORD_RE.findall(_strip(" ".join(c["text"] for c in cues)).lower())


def timed_words(path: str) -> list[tuple[str, float]]:
    """Same words, each tagged with the start time of the cue it came from.
    Used to locate *where* in the episode content is missing."""
    out = []
    for c in srt.parse(Path(path).read_text(encoding="utf-8", errors="ignore")):
        for w in WORD_RE.findall(_strip(c["text"]).lower()):
            out.append((w, c["start"]))
    return out


def compare(ref_path: str, ours_path: str) -> dict:
    ref_timed = timed_words(ref_path)
    ref = [w for w, _ in ref_timed]
    ours = words(ours_path)
    if not ref:
        raise SystemExit(f"No words parsed from reference: {ref_path}")

    sm = difflib.SequenceMatcher(None, ref, ours, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())

    # Where the misses fall matters more than how many there are. Gaps
    # clustered at the head and tail are almost always the OP and ED, which
    # are sung and which we neither can nor should transcribe. Gaps spread
    # through the middle are real dialogue being lost.
    missing_times = []
    for tag, i1, i2, _, _ in sm.get_opcodes():
        if tag in ("delete", "replace"):
            missing_times.extend(t for _, t in ref_timed[i1:i2])

    return {
        "ref_words": len(ref),
        "missing_times": missing_times,
        "episode_end": ref_timed[-1][1] if ref_timed else 0,
        "our_words": len(ours),
        "matched": matched,
        "accuracy": 100 * matched / len(ref),
        "coverage": 100 * len(ours) / len(ref),
        "missing_runs": [
            " ".join(ref[i1:i2])
            for tag, i1, i2, _, _ in sm.get_opcodes()
            if tag in ("delete", "replace") and i2 - i1 >= 4
        ],
    }


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: score.py <reference.srt> <ours.srt> [--show-missing]")

    r = compare(sys.argv[1], sys.argv[2])

    print(f"reference   {r['ref_words']:>6} words")
    print(f"ours        {r['our_words']:>6} words")
    print(f"matched     {r['matched']:>6} words")
    print()
    print(f"accuracy    {r['accuracy']:>6.1f}%   (reference words we got right)")
    print(f"coverage    {r['coverage']:>6.1f}%   (how much of the script we produced)")

    if "--timeline" in sys.argv and r["missing_times"]:
        end = r["episode_end"] or 1
        buckets = 12
        counts = [0] * buckets
        for t in r["missing_times"]:
            counts[min(int(t / end * buckets), buckets - 1)] += 1
        peak = max(counts) or 1
        print("\n where the gaps fall")
        for i, n in enumerate(counts):
            lo = int(i * end / buckets)
            bar = "#" * int(28 * n / peak)
            print(f"  {lo//60:>3}:{lo%60:02d}  {bar:<28} {n}")
        head_tail = counts[0] + counts[1] + counts[-1] + counts[-2]
        pct = 100 * head_tail / max(sum(counts), 1)
        print(f"\n  {pct:.0f}% of missing words are in the first or last ~4 minutes")
        print("  (high here means OP/ED songs, not lost dialogue)")

    if r["missing_runs"]:
        print(f"\n{len(r['missing_runs'])} stretch(es) of 4+ words missing or wrong")
        if "--show-missing" in sys.argv:
            for run in r["missing_runs"][:40]:
                print(f"  - {run[:90]}")
        else:
            print("  pass --show-missing to list them")


if __name__ == "__main__":
    main()
