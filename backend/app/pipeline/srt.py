from pathlib import Path


def _ts(seconds: float, sep: str = ",") -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(cues, 1):
        blocks.append(f"{i}\n{_ts(c['start'])} --> {_ts(c['end'])}\n{c['text']}\n")
    return "\n".join(blocks)


def to_vtt(cues: list[dict]) -> str:
    body = "\n".join(
        f"{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}\n{c['text']}\n" for c in cues
    )
    return "WEBVTT\n\n" + body


def write(cues: list[dict], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(to_srt(cues), encoding="utf-8")
    return dest


def parse(text: str) -> list[dict]:
    """Read an SRT back in, so the editor can round-trip a saved file."""
    cues = []
    for block in text.strip().split("\n\n"):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        timing = next((l for l in lines if "-->" in l), None)
        if not timing:
            continue
        idx = lines.index(timing)
        start, end = [_parse_ts(p.strip()) for p in timing.split("-->")[:2]]
        cues.append({"start": start, "end": end, "text": "\n".join(lines[idx + 1:])})
    return cues


def _parse_ts(value: str) -> float:
    value = value.replace(",", ".").split(" ")[0]
    parts = value.split(":")
    h, m, s = (["0"] * (3 - len(parts))) + parts
    return int(h) * 3600 + int(m) * 60 + float(s)
