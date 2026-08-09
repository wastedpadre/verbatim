"""One interface over the three LLM vendors the polish pass can use.

Every provider here answers the same question: given a system prompt and a
numbered list of cues, return a JSON array of the lines to change. The
guards in polish.py validate whatever comes back, so a provider only has to
produce a list -- it does not have to produce a *good* list.

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

    Gemini 3.x takes thinkingLevel ("minimal" is the floor for Flash -- it
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


def _gemini_test() -> tuple[bool, str]:
    r = requests.post(
        f"{GEMINI_ENDPOINT}/{config.POLISH_MODEL}:generateContent",
        params={"key": config.GEMINI_API_KEY},
        json={"contents": [{"parts": [{"text": "Reply with: OK"}]}]},
        timeout=25,
    )
    if r.status_code == 200:
        return True, f"{config.POLISH_MODEL} responded normally."
    try:
        msg = r.json().get("error", {}).get("message", r.text[:200])
    except ValueError:
        msg = r.text[:200]
    if r.status_code == 404:
        return False, f"Model not available. {msg}"
    if r.status_code in (400, 401, 403):
        return False, f"Key rejected. {msg}"
    return False, f"HTTP {r.status_code}. {msg}"


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
    a 400 on every window -- and the model picker lets anyone select one.
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


def _openai_test() -> tuple[bool, str]:
    # No max_tokens: the reasoning models renamed it to max_completion_tokens
    # and reject the old spelling, and a five-word reply needs no cap anyway.
    resp = _openai_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "user", "content": "Reply with: OK"}],
    )
    said = (resp.choices[0].message.content or "").strip()
    return True, f"{config.OPENAI_MODEL} responded: {said!r}"


def _openai_models() -> list[str]:
    names = sorted(m.id for m in _openai_client().models.list())
    # Chat models only -- the key can also see embeddings, audio and image
    # models, none of which can answer this prompt.
    chat = [n for n in names if n.startswith("gpt") or n.startswith("o")]
    return chat or names


# ------------------------------------------------------------ Anthropic

def _anthropic_client():
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=90.0)


def _anthropic_complete(prompt: str) -> list[dict]:
    # No temperature: current Claude models reject sampling parameters
    # outright, so sending temperature=0 for parity with the other two
    # providers would be a 400 on every window.
    #
    # Thinking is left on at low effort rather than disabled. Disabling it
    # is the documented cause of <thinking> tags leaking into the visible
    # response, and this response is parsed as JSON -- a leaked tag is a
    # dropped window.
    resp = _anthropic_client().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": CHANGES_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    # Safety classifiers answer with HTTP 200 and an empty content list, so
    # indexing content[0] unconditionally would raise on a refusal.
    if resp.stop_reason == "refusal":
        log.warning("polish: Anthropic declined the window, keeping originals")
        return []
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse(text)


def _anthropic_test() -> tuple[bool, str]:
    resp = _anthropic_client().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=16,
        messages=[{"role": "user", "content": "Reply with: OK"}],
    )
    if resp.stop_reason == "refusal":
        return False, "The model declined a trivial prompt -- check the key's workspace."
    said = "".join(b.text for b in resp.content if b.type == "text").strip()
    return True, f"{config.ANTHROPIC_MODEL} responded: {said!r}"


def _anthropic_models() -> list[str]:
    return [m.id for m in _anthropic_client().models.list()]


# --------------------------------------------------------------- dispatch

_PROVIDERS = {
    "gemini": (_gemini_complete, _gemini_test, _gemini_models),
    "openai": (_openai_complete, _openai_test, _openai_models),
    "anthropic": (_anthropic_complete, _anthropic_test, _anthropic_models),
}

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

    Model IDs get retired regularly and a stale one fails with a 404 only
    once a job reaches the polish stage, minutes in. This surfaces it in a
    couple of seconds instead.
    """
    if not config.polish_key():
        return False, f"No {config.POLISH_PROVIDER} API key set."
    try:
        return _pick(1)()
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc)


def models() -> list[str]:
    """Which models this key can actually call -- beats guessing."""
    if not config.polish_key():
        raise ProviderError(f"No {config.POLISH_PROVIDER} API key set.")
    try:
        return _pick(2)()
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
