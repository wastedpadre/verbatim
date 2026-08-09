import subprocess
from pathlib import Path


def build_filter(channels: int) -> str:
    """Choose a downmix that favours dialogue.

    This is the single most important setting in the pipeline for dub audio.
    In a 5.1 mix the dialogue is placed almost entirely in the centre channel;
    music sits in front L/R, effects in the surrounds, rumble in LFE. Letting
    ffmpeg do a default `-ac 1` downmix averages all six together, so the
    model receives dialogue with the full score and sound design layered on
    top at equal weight. Quiet lines vanish under scoring and non-speech
    sounds compete with speech.

    Taking the centre channel alone removes most of that interference.
    """
    if channels >= 6:
        pan = "pan=mono|c0=FC"
    elif channels == 2:
        pan = "pan=mono|c0=0.5*FL+0.5*FR"
    else:
        pan = "pan=mono|c0=c0"
    # Dub mixes are cinematic: whispered lines and shouted ones can be 30 dB
    # apart. Levelling that out keeps quiet dialogue above the model's floor.
    return f"{pan},dynaudnorm=f=200:g=15:p=0.9:m=8"


def extract_audio(src: Path, stream_index: int, dest: Path,
                  channels: int = 2) -> Path:
    """Pull one audio stream down to 16 kHz mono PCM, using a dialogue-forward
    downmix rather than a flat channel average."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-map", f"0:{stream_index}",
         "-af", build_filter(channels),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
         "-vn", "-sn", "-dn", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def extract_subtitle(src: Path, stream_index: int, dest: Path) -> Path | None:
    """Dump a text subtitle stream to SRT so we can mine it for names."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-map", f"0:{stream_index}", "-c:s", "srt", str(dest)],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None
    return dest if dest.exists() and dest.stat().st_size > 0 else None


def audio_start_offset(src: Path, stream_index: int) -> float:
    """Some WEB-DL muxes start audio a few seconds after video. If we ignore
    that, every cue lands early by a constant amount."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", str(stream_index),
         "-show_entries", "stream=start_time", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    try:
        return max(0.0, float(out.stdout.strip()))
    except ValueError:
        return 0.0
