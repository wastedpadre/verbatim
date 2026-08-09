"""One interface over the three LLM vendors the polish pass can use.

Every provider here answers the same question: given a system prompt and a
numbered list of cues, return a JSON array of the lines to change. The
guards in polish.py validate whatever comes back, so a provider only has to
produce a list; it does not have to produce a *good* list.

Three functions per provider, because the Settings page needs all three:
`complete` runs a window, `test` is the connection check, and `models` fills
the model picker so nobody has to guess an ID that the vendor may have
retired last month.

Gemini stays on plain `requests`: it is the original path, its REST shape is
small, and adding google-genai to the image to send one POST isn't worth it.
OpenAI and Anthropic use their official SDKs, which is what both vendors
support and which gets retries and typed errors for free.
"""
import json
import logging
import re

import requests

from .. import config

log = logging.getLogger("verbatim.providers")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

# The shape every provider is asked for. Gemini and OpenAI take it as a
# response schema; Anthropic takes it as a structured-output format. Keeping
# one definition means the three cannot drift apart.
CHANGES_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["i", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["changes"],
    "additionalProperties": False,
}

# Sent to every provider so the model knows the envelope. The task itself is
# described by polish.SYSTEM.
ENVELOPE = ('Return a JSON object of the form {"changes": [{"i": <index>, '
            '"text": "<corrected line>"}]}. Use an empty array when nothing '
            "needs fixing.")


class ProviderError(RuntimeError):
    """Anything that stopped a window from being answered. polish.py treats
    this as 'keep the originals' rather than failing the job."""


def _parse(raw: str) -> list[dict]:
    """Pull the changes array out of whatever the model returned.

    Structured output modes make this reliable, but a model that ignores the
    schema and wraps the object in a markdown fence still parses here rather
    than losing the window.
    """
    if not raw:
        return []
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("polish: %s returned non-JSON, skipping window",
                    config.POLISH_PROVIDER)
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("changes", [])
    return parsed if isinstance(parsed, list) else []


# --------------------------------------------------------------- Gemini

def _gemini_thinking() -> dict:
    """Thinking controls differ by model generation and can't be combined.

    Gemini 3.x takes thinkingLevel ("minimal" is the floor for Flash, and it
    cannot be switched off entirely). Gemini 2.5 takes thinkingBudget, where
    0 does disable it. Sending both returns a 400, so pick one by model name.
    """
    if not config.POLISH_THINKING:
        return {}
    model = config.POLISH_MODEL
    if "-3" in model or "latest" in model:
        return {"thinkingConfig": {"thinkingLevel": config.POLISH_THINKING}}
    if "2.5" in model or "2-5" in model:
        return {"thinkingConfig": {"thinkingBudget": 0}}
    return {}


def _gemini_complete(prompt: str) -> list[dict]:
    resp = requests.post(
        f"{GEMINI_ENDPOINT}/{config.POLISH_MODEL}:generateContent",
        params={"key": config.GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                **_gemini_thinking(),
                # Deprecated on newer Gemini models (ignored rather than
                # rejected), but harmless to send and still honoured by
                # older ones.
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return _parse(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError):
        log.warning("polish: unexpected Gemini response shape, skipping window")
        return []


def _gemini_models() -> list[str]:
    r = requests.get(GEMINI_ENDPOINT,
                     params={"key": config.GEMINI_API_KEY}, timeout=25)
    r.raise_for_status()
    names = [
        m["name"].replace("models/", "")
        for m in r.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    # Flash-class models are the sensible choice for constrained substitution.
    preferred = [n for n in names if "flash" in n or "lite" in n]
    return preferred or names


# --------------------------------------------------------------- OpenAI

def _openai_client():
    from openai import OpenAI
    return OpenAI(api_key=config.OPENAI_API_KEY, timeout=90)


def _openai_reasoning() -> bool:
    """Whether the selected model is one of the reasoning families.

    They reject `temperature` outright, so sending it for determinism costs
    a 400 on every window, and the model picker lets anyone select one.
    """
    m = config.OPENAI_MODEL.lower()
    return m.startswith("o") or m.startswith("gpt-5")


def _openai_complete(prompt: str) -> list[dict]:
    extra = {} if _openai_reasoning() else {"temperature": 0}
    resp = _openai_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "changes", "strict": True,
                            "schema": CHANGES_SCHEMA},
        },
        **extra,
    )
    return _parse(resp.choices[0].message.content or "")


# A real key sees ~110 models once you keep everything starting with "gpt"
# or "o", and most of them cannot answer a text prompt. These substrings mark
# the families to drop so the picker stays usable.
_OPENAI_NON_CHAT = ("tts", "transcribe", "audio", "realtime", "image",
                    "embedding", "moderation", "dall-e", "whisper",
                    "search-preview", "computer-use")


def _openai_models() -> list[str]:
    names = sorted(m.id for m in _openai_client().models.list())
    chat = [
        n for n in names
        if (n.startswith("gpt") or n.startswith("o"))
        and not any(t in n for t in _OPENAI_NON_CHAT)
        # Dated snapshots duplicate their alias (gpt-4.1-mini-2025-04-14
        # alongside gpt-4.1-mini) and double the list for no benefit.
        and not re.search(r"-\d{4}-\d{2}-\d{2}$", n)
    ]
    return chat or names


# ------------------------------------------------------------ Anthropic

def _anthropic_client():
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=90.0)


# Whether a given Claude model accepts the thinking/effort controls. Learned
# at runtime rather than hardcoded: the split is by model generation, and a
# static list would mislabel every model released after this code was written.
_anthropic_controls: dict[str, bool] = {}


def _anthropic_unsupported_controls(exc: Exception) -> bool:
    """True when a 400 is complaining about thinking or effort specifically."""
    text = str(exc).lower()
    return "400" in text and ("thinking" in text or "effort" in text)


def _anthropic_send(model: str, prompt: str, controls: bool, max_tokens: int):
    """One request. `controls` adds the current-generation-only parameters.

    No temperature either way: current models reject sampling parameters
    outright, so sending temperature=0 for parity with the other two
    providers would be a 400 on every window.
    """
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        # Structured output is supported across every model worth using
        # here, so the JSON shape is guaranteed on both paths.
        "output_config": {"format": {"type": "json_schema",
                                     "schema": CHANGES_SCHEMA}},
    }
    if controls:
        # Thinking stays on at low effort rather than disabled: disabling it
        # is the documented cause of <thinking> tags leaking into the visible
        # response, and this response is parsed as JSON, so a leaked tag
        # costs the whole window.
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"]["effort"] = "low"
    return _anthropic_client().messages.create(**kwargs)


def _anthropic_call(prompt: str, max_tokens: int):
    """Send, and on a controls-related 400 retry once without them.

    Haiku 4.5 and older reject `thinking` and `effort`; the current
    generation wants them, because otherwise thinking defaults on at high
    effort and this task pays for reasoning it does not need. Trying the
    modern shape first and remembering the answer gets both right without a
    model list to maintain.
    """
    model = config.ANTHROPIC_MODEL
    controls = _anthropic_controls.get(model, True)
    try:
        return _anthropic_send(model, prompt, controls, max_tokens)
    except Exception as exc:  # noqa: BLE001 - narrowed immediately below
        if not (controls and _anthropic_unsupported_controls(exc)):
            raise
        log.info("polish: %s rejects thinking/effort, retrying without", model)
        _anthropic_controls[model] = False
        return _anthropic_send(model, prompt, False, max_tokens)


def _anthropic_complete(prompt: str) -> list[dict]:
    resp = _anthropic_call(prompt, 16000)
    # Safety classifiers answer with HTTP 200 and an empty content list, so
    # indexing content[0] unconditionally would raise on a refusal.
    if resp.stop_reason == "refusal":
        log.warning("polish: Anthropic declined the window, keeping originals")
        return []
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse(text)


def _anthropic_models() -> list[str]:
    return [m.id for m in _anthropic_client().models.list()]


# --------------------------------------------------------------- dispatch

_PROVIDERS = {
    "gemini": (_gemini_complete, _gemini_models),
    "openai": (_openai_complete, _openai_models),
    "anthropic": (_anthropic_complete, _anthropic_models),
}

# Deliberately shaped like a real window so the connection check exercises
# the real request. A bare "reply with OK" probe passed against a model that
# then 400'd on the first actual job: a green check that doesn't cover the
# path it claims to check is worse than no check at all.
TEST_PROMPT = (
    "This is a connection test, not a transcript. Make no changes.\n"
    + ENVELOPE
    + "\nLines:\n0: The quick brown fox jumps over the lazy dog."
)

# Where each vendor issues keys, so the Settings hint can point somewhere.
KEY_SOURCES = {
    "gemini": "aistudio.google.com",
    "openai": "platform.openai.com/api-keys",
    "anthropic": "console.anthropic.com",
}


def _pick(index: int):
    fns = _PROVIDERS.get(config.POLISH_PROVIDER)
    if fns is None:
        raise ProviderError(f"Unknown polish provider {config.POLISH_PROVIDER!r}")
    return fns[index]


def complete(prompt: str) -> list[dict]:
    """Run one window through the selected provider.

    Every vendor-specific exception is flattened to ProviderError so the
    caller doesn't need to know which SDK is in play to catch a failure.
    """
    try:
        return _pick(0)(prompt)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - vendor SDKs raise their own trees
        raise ProviderError(str(exc)) from exc


def test() -> tuple[bool, str]:
    """Live connection check. Returns (ok, human-readable detail).

    Runs the same function a real window runs, so a stale model ID, a
    rejected key or an unsupported parameter surfaces here in a couple of
    seconds instead of minutes into a job.
    """
    if not config.polish_key():
        return False, f"No {config.POLISH_PROVIDER} API key set."
    try:
        _pick(0)(TEST_PROMPT)
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc)

    note = ""
    if (config.POLISH_PROVIDER == "anthropic"
            and not _anthropic_controls.get(config.ANTHROPIC_MODEL, True)):
        note = " (this model predates thinking/effort, so those were dropped)"
    return True, f"{config.polish_model()} accepted a real polish request{note}."


def models() -> list[str]:
    """Which models this key can actually call. Beats guessing."""
    if not config.polish_key():
        raise ProviderError(f"No {config.POLISH_PROVIDER} API key set.")
    try:
        return _pick(1)()
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(_explain(exc)) from exc


def _explain(exc: Exception) -> str:
    """Turn a vendor exception into something worth showing in the UI.

    Matching on class *name* rather than importing each SDK's exception tree
    keeps this working when only one of the two optional SDKs is installed.
    """
    name = type(exc).__name__
    text = str(exc)
    if "NotFound" in name:
        return (f"Model {config.polish_model()!r} is not available to this key. "
                "Use 'List available models'.")
    if "Authentication" in name or "PermissionDenied" in name:
        return f"Key rejected. {text}"
    if "RateLimit" in name:
        return f"Rate limited. {text}"
    if "Connection" in name or isinstance(exc, requests.RequestException):
        return f"Could not reach {config.POLISH_PROVIDER}. {text}"
    return text
