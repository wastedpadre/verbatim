"""Optional LLM pass that repairs words the decoder misheard.

The decoder runs with `condition_on_previous_text=False`, which stops a single
hallucinated segment from poisoning everything after it. The cost is that each
segment is decoded without knowing what came before, so the model can't use
context to tell that "refer to" fits and "revert to" doesn't. Both are real
words, correctly spelled, acoustically close. Nothing earlier in the pipeline
can catch that.

This pass reads a window of cues at once and fixes only that class of error.

The hard part isn't making corrections, it's stopping the model from making
*improvements*. A language model asked to fix a transcript will happily tidy
grammar, smooth phrasing, and invent plausible dialogue for anything it finds
unclear. That produces subtitles that read well and don't match the audio,
which is worse than leaving the error in. Four independent guards below.
"""
import concurrent.futures
import difflib
import json
import logging
import re

import requests

from .. import config

log = logging.getLogger("verbatim.polish")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = """You are correcting an automatic transcription of an English anime dub.

Fix ONLY words that were clearly misheard by speech recognition. Typical cases:
a real word that is acoustically similar to the correct one but makes no sense
in context, or a proper noun spelled wrong.

You MUST NOT:
- rephrase, shorten, expand, or improve any line
- fix grammar, punctuation style, or awkward phrasing that a person actually said
- add or remove dialogue, or invent words for anything unclear
- merge or split lines, or change their order
- change a line you are not confident is wrong

Dub dialogue is often clipped, informal, or ungrammatical on purpose. That is
not an error. If you are not certain a word was misheard, leave the line alone.

Return ONLY a JSON array of the lines you changed, each as
{"i": <index>, "text": "<corrected line>"}.
Return [] if nothing needs fixing. No prose, no markdown fences."""


def _thinking_config() -> dict:
    """Thinking controls differ by model generation and can't be combined.

    Gemini 3.x takes thinkingLevel ("minimal" is the floor for Flash -- it
    cannot be switched off entirely). Gemini 2.5 takes thinkingBudget, where
    0 does disable it. Sending both returns a 400, so pick one by model name.

    Either way we want the least reasoning available: this is constrained
    find-and-replace with hard guards, and thinking is latency we don't need.
    """
    if not config.POLISH_THINKING:
        return {}
    model = config.POLISH_MODEL
    if "-3" in model or "latest" in model:
        return {"thinkingConfig": {"thinkingLevel": config.POLISH_THINKING}}
    if "2.5" in model or "2-5" in model:
        return {"thinkingConfig": {"thinkingBudget": 0}}
    return {}


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _windows(n: int, size: int, overlap: int):
    """Yield (start, end) spans covering n cues with the given overlap."""
    if n <= size:
        yield (0, n)
        return
    step = max(size - overlap, 1)
    start = 0
    while start < n:
        yield (start, min(start + size, n))
        if start + size >= n:
            break
        start += step


def _call(cues: list[dict], terms: list[str], offset: int) -> list[dict]:
    """Ask the model about one window. Returns raw change proposals."""
    numbered = "\n".join(
        f'{i + offset}: {c["text"]}'.replace("\n", " ") for i, c in enumerate(cues)
    )
    glossary = ""
    if terms:
        glossary = ("\nThese names are spelled correctly. Never alter them, and "
                    "prefer them when a similar-sounding word appears:\n"
                    + ", ".join(terms[:60]) + "\n")

    prompt = f"{SYSTEM}\n{glossary}\nLines:\n{numbered}"

    resp = requests.post(
        ENDPOINT.format(model=config.POLISH_MODEL),
        params={"key": config.GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                **_thinking_config(),
                # Deprecated on newer Gemini models (ignored rather than
                # rejected), but harmless to send and still honoured by
                # older ones. If it is ignored, polish output stops being
                # deterministic between runs -- the guards below don't care,
                # since each proposed change is validated on its own merits.
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        log.warning("polish: unexpected response shape, skipping window")
        return []

    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        log.warning("polish: model returned non-JSON, skipping window")
        return []

    return parsed if isinstance(parsed, list) else []


def _acceptable(original: str, proposed: str) -> tuple[bool, str]:
    """Guards. Every one of these has to pass before a change is applied."""
    o, p = original.replace("\n", " ").strip(), proposed.replace("\n", " ").strip()

    if not p:
        return False, "empty"
    if o == p:
        return False, "identical"

    # Guard 1: a genuine fix changes a word or two, not the whole line.
    sim = _similarity(o, p)
    if sim < config.POLISH_MIN_SIMILARITY:
        return False, f"rewrote too much (similarity {sim:.2f})"

    # Guard 2: word count shouldn't move much. Big swings mean content was
    # added or dropped rather than corrected.
    delta = abs(len(o.split()) - len(p.split()))
    if delta > config.POLISH_MAX_WORD_DELTA:
        return False, f"word count moved by {delta}"

    # Guard 3: refuse wholesale case or punctuation-only churn, which is
    # style editing rather than error correction.
    if re.sub(r"[^\w]", "", o).lower() == re.sub(r"[^\w]", "", p).lower():
        return False, "punctuation-only change"

    return True, ""


def polish(cues: list[dict], terms: list[str]) -> tuple[list[dict], list[dict]]:
    """Return (cues, changes). Timings are never touched — only cue text.

    Cues are returned in the same order and the same count as they came in.
    """
    if not config.POLISH_ENABLED or not config.GEMINI_API_KEY or not cues:
        return cues, []

    out = [dict(c) for c in cues]
    changes: list[dict] = []
    seen: set[int] = set()

    spans = list(_windows(len(cues), config.POLISH_WINDOW, config.POLISH_OVERLAP))
    log.info("polish: %d cues in %d windows, %d at a time",
             len(cues), len(spans), config.POLISH_CONCURRENCY)

    def fetch(span):
        start, end = span
        try:
            return span, _call(cues[start:end], terms, start)
        except requests.RequestException as exc:
            # A polish failure must never fail the job -- unpolished cues are
            # still perfectly usable output.
            log.warning("polish: window %d-%d failed (%s), keeping originals",
                        start, end, exc)
            return span, []

    # Windows don't depend on each other, so fan them out. Sequentially this
    # stage took minutes; the API round trip dominates, not local work.
    results = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=config.POLISH_CONCURRENCY) as pool:
        for done, (span, proposals) in enumerate(
                pool.map(fetch, spans), start=1):
            results.append((span, proposals))
            log.info("polish: window %d/%d done", done, len(spans))

    # Apply in window order regardless of completion order, so the result is
    # deterministic and the first decision for a cue wins.
    for (start, end), proposals in results:
        for item in proposals:
            if not isinstance(item, dict):
                continue
            idx, proposed = item.get("i"), item.get("text")
            if not isinstance(idx, int) or not isinstance(proposed, str):
                continue
            if not (start <= idx < end) or idx in seen:
                continue

            ok, reason = _acceptable(cues[idx]["text"], proposed)
            seen.add(idx)
            if not ok:
                log.info("polish: rejected cue %d (%s)", idx, reason)
                changes.append({"cue": idx, "from": cues[idx]["text"],
                                "to": proposed, "applied": False, "reason": reason})
                continue

            out[idx]["text"] = proposed.strip()
            changes.append({"cue": idx, "from": cues[idx]["text"],
                            "to": proposed.strip(), "applied": True, "reason": ""})

    applied = sum(1 for c in changes if c["applied"])
    log.info("polish: %d applied, %d rejected", applied, len(changes) - applied)
    return out, changes
