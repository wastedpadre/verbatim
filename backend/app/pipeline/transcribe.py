"""Run the acoustic model over the extracted dub audio.

Anime dubs are a hostile input: dense music beds, sound effects at dialogue
volume, long silent stretches over scenery, and shouted lines that clip. The
settings here are tuned for that rather than for podcast audio.
"""
import threading
from pathlib import Path

from faster_whisper import WhisperModel

from .. import config

_model = None
_model_lock = threading.Lock()


def get_model() -> WhisperModel:
    """Load once and keep resident — a cold large-v3 load costs ~20s and
    several GB of VRAM churn."""
    global _model
    with _model_lock:
        if _model is None:
            _model = WhisperModel(
                config.MODEL_SIZE,
                device=config.DEVICE,
                compute_type=config.COMPUTE_TYPE,
                download_root=str(config.MODEL_DIR),
            )
        return _model


# ctranslate2 reports int8 compute types as available on cards whose cuBLAS
# has no int8 GEMM path -- get_supported_compute_types("cuda") lists
# int8_float16 on Blackwell, then the first matmul dies. There is no cheap way
# to detect that up front, so translate the error instead of preflighting it.
_CUBLAS_HINT = (
    "Your GPU cannot run the {ct} precision (cuBLAS has no int8 path for it, "
    "even though the library advertises one). Set precision to float16 under "
    "Settings -> Speech model and restart the container."
)


def _explain_cuda(exc: Exception) -> Exception:
    text = str(exc)
    if "CUBLAS" in text.upper() and "int8" in config.COMPUTE_TYPE:
        return RuntimeError(_CUBLAS_HINT.format(ct=config.COMPUTE_TYPE))
    return exc


def transcribe(audio_path: Path, prompt: str, on_progress=None) -> list[dict]:
    """Yield word-timed segments. on_progress(seconds_done, text) is called
    as each segment decodes so the UI can show live output."""
    # faster-whisper returns a generator, so the encoder — and the matmul
    # that fails on an unsupported precision — runs during iteration, not on
    # the call. The whole thing has to be inside the try.
    try:
        return _run(get_model(), audio_path, prompt, on_progress)
    except Exception as exc:  # noqa: BLE001 - ctranslate2 raises bare RuntimeError
        raise _explain_cuda(exc) from exc


def _run(model, audio_path: Path, prompt: str, on_progress) -> list[dict]:
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=config.BEAM_SIZE,
        word_timestamps=True,
        initial_prompt=prompt or None,
        # Each segment is decoded independently. Left on, a single hallucinated
        # line poisons every subsequent one — that's the "thanks for watching"
        # death spiral you see on music-heavy content.
        condition_on_previous_text=False,
        vad_filter=config.VAD_FILTER,
        vad_parameters={
            "threshold": config.VAD_THRESHOLD,
            "min_speech_duration_ms": config.VAD_MIN_SPEECH_MS,
            "min_silence_duration_ms": config.VAD_MIN_SILENCE_MS,
            "speech_pad_ms": config.VAD_SPEECH_PAD_MS,
        } if config.VAD_FILTER else None,
        # Retry a segment at higher temperature only when the greedy decode
        # looks degenerate.
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,
        # Both raised: these gate whole segments out, and the previous
        # values discarded legitimate dialogue in music-heavy scenes.
        log_prob_threshold=-1.5,
        no_speech_threshold=0.85,
    )

    out = []
    for seg in segments:
        words = [
            {"start": w.start, "end": w.end, "word": w.word,
             "prob": getattr(w, "probability", 1.0)}
            for w in (seg.words or [])
            if w.start is not None and w.end is not None
        ]
        if not words:
            continue
        out.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "words": words,
            "no_speech_prob": seg.no_speech_prob,
            "avg_logprob": seg.avg_logprob,
            "compression_ratio": seg.compression_ratio,
        })
        if on_progress:
            on_progress(seg.end, seg.text.strip())

    return out
