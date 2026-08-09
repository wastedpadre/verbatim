# Verbatim

Generates English closed captions from the **English dub audio track** of your
own media. Dubtitles are notoriously hard to find because almost every
"English" subtitle file floating around is a translation of the *Japanese*
audio; the dub script is a different adaptation and rarely matches. This
transcribes what the dub actually says.

Runs as one container. React UI, FastAPI backend, faster-whisper on your GPU.

---

## What makes the output usable

Straight Whisper on anime is mediocre. Four things here fix most of it:

**Track selection.** Dual-audio releases have two audio streams and several
subtitle streams. Picking the wrong one silently produces nonsense, so
`probe.py` scores streams on language tags, titles, and channel count, and
tells you in the UI which one it chose.

**Glossary priming.** Your releases almost always carry the English *sub*
track. The dub rewrites the dialogue so its wording is useless to us, but
every character name, place, and invented term is in there, spelled correctly.
Verbatim extracts only that vocabulary, feeds it to the decoder as a prompt,
and fuzzy-corrects near-misses afterward. This is the single biggest quality
win; it's the difference between "Sakuro" and "Sakura" for the whole episode.

**Hallucination control.** Music beds and long silent scenery shots make
Whisper invent confident, well-formed filler. `condition_on_previous_text` is
off so one bad segment can't poison the rest, VAD gates non-speech, and a
cleanup pass drops known artifacts and collapses repetition loops.

**Readable cues.** The model segments for accuracy, not readability. Cues get
rebuilt from word-level timings against real captioning rules: 42 characters
per line, 2 lines max, 20 characters per second, no overlaps, breaks at clause
boundaries rather than mid-phrase.

**A caption editor for the rest.** No ASR pass is perfect. Click **Edit
captions** on any finished job to fix what's left, with the tool built around
the failure mode that actually happens.

**A live log.** **Show logs** in the top bar streams the server log into the
page, so diagnosing a bad run doesn't mean opening a terminal.

---

## The caption editor

When the decoder gets a name wrong, it gets it wrong *the same way in every
cue*. So the editor leads with find-and-replace rather than manual editing:
type the mistake, click the correct spelling from the glossary chips (those
are the names pulled from your sub track), replace all. Forty fixes, one
click.

Everything else is there for the leftovers:

- **Live validation** against the same rules the backend shaped with: line
  length, line count, reading speed, overlaps, cues too short to read. Flagged
  cues get an amber edge.
- **Show flagged only** filters to just the cues that break a rule, so you're
  not scrolling 400 lines looking for the 6 that need work.
- **Split / Merge / Delete** per cue. Split divides the duration in proportion
  to the text so neither half reads twice as fast as the other.
- **Editable timecodes** accepting both `00:01:23.450` and SRT-style
  `00:01:23,450`.
- **Save & rewrap** re-flows every line to the character limit, preserving
  breaks you typed deliberately.
- `Cmd/Ctrl+S` saves. Closing with unsaved changes warns first.

The server re-validates everything on save regardless of what the editor
sends (sorts by start time, repairs overlaps, rejects reversed timecodes),
because this file gets written straight into your media folder.

---

## Quick start

```bash
cp .env.example .env      # edit MEDIA_ROOTS if needed
docker compose up -d --build
```

Open `http://<host>:8080`, browse to a folder, tick episodes, hit
**Caption episodes**.

Output lands next to the video as `Episode.en.dubtitles.srt`. No remux needed.

**Selecting it in your player.** Jellyfin detects the sidecar and turns it on by
itself. **Plex detects it but will not switch to it**: during playback, open the
subtitle menu and choose **English (SRT External)**. To stop doing that every
episode, set it once per series under *Settings → Subtitles*, or enable
*Automatically select subtitles* in your Plex player preferences. If it isn't
listed at all, Plex hasn't rescanned the folder yet: *Scan Library Files* on
that show.

### On Unraid

**See `UNRAID.md`**: it's the full walkthrough, and Unraid's GPU passthrough
differs enough from stock Docker that the generic instructions will quietly
leave you running on CPU.

Short version: install the **Nvidia Driver** plugin and reboot, install
**Docker Compose Manager**, copy the source to
`/mnt/user/appdata/verbatim-src`, then Compose Up. An Unraid Docker template
(`unraid-template.xml`) is included if you'd rather manage it from the Docker
tab.

First run downloads the model (~3 GB for large-v3) into `/config/models`.

---

## Automating it

Point Sonarr at the webhook and new episodes caption themselves on import.
**Settings → Sonarr webhook** in the app shows the exact URL for your host with
a copy button; in Sonarr it goes under **Settings → Connect → + → Webhook**,
method POST, triggers **On Import** and **On Upgrade**.

One catch: Sonarr sends the path *as Sonarr sees it*. If Sonarr's path is
`/tv/Show/Episode.mkv` and Verbatim's is `/media/Show/Episode.mkv`, the webhook
can't find the file, and the POST still succeeds, so nothing looks wrong.
Either mount the same host path at the same container path in both (simplest),
or set a translation: `SONARR_PATH_MAP=/tv:/media`. When a path doesn't
resolve, the container log says `is not a file here` and names both sides.

Sonarr's own **Test** button always succeeds: it sends no file path, so it
proves the URL is reachable and nothing else.

### Captioning only part of your library

Two ways, and the first is better because nothing leaves Sonarr:

1. **Tag the connection.** Tag your anime series in Sonarr, then set that same
   tag on the webhook under **Connect → Webhook → Tags**. Sonarr only fires for
   tagged series, so a new sitcom import never reaches Verbatim at all.
2. **Filter here.** Set `SONARR_SERIES_TYPES=anime` (or **Settings → Sonarr
   webhook → Only these series types**). Sonarr classifies every series as
   `standard`, `daily` or `anime` and sends that on the webhook; anything else
   is skipped and logged with the type it saw. Blank accepts everything.

Use the second as a backstop for the first: a series added without the tag
still gets filtered. If you set a type filter and *nothing* gets captioned,
check the log: it prints the series type it actually received, which is
`unset` on Sonarr versions that don't send the field.

---

## Word repair providers

The optional repair pass runs on **Gemini, OpenAI or Anthropic**: set
`POLISH_PROVIDER`, or switch it live under **Settings → Word repair**. Each
provider keeps its own key and model, so switching doesn't leave a model ID
pointing at the wrong API.

| Provider | Key from | Default model |
|---|---|---|
| `gemini` | aistudio.google.com | `gemini-flash-latest` |
| `openai` | platform.openai.com/api-keys | `gpt-4.1-mini` |
| `anthropic` | console.anthropic.com | `claude-opus-5` |

All three are verified against live keys. Gemini has the most mileage, having
run across real episodes; OpenAI and Anthropic are confirmed working end to
end but have seen less use. Run **Test connection** after setting a key: it
sends one real request in the same shape a job uses, so a bad key, a retired
model ID or a parameter the model doesn't accept shows up in seconds.

The task is constrained substitution, not reasoning, so the cheapest model in a
family is usually enough: `claude-haiku-4-5` instead of Opus, a mini-class
model on OpenAI. **Test connection** sends one throwaway prompt so a bad key or
a retired model ID surfaces in seconds rather than minutes into a job, and
**List available models** asks the provider what your key can actually call.

Whichever provider is selected, the same four guards apply to every proposed
change, and a provider failure never fails the job; the window keeps its
original cues.

---

## Tuning

Everything lives in `.env`, and almost all of it is also editable under
**Settings**: including the VAD parameters, which apply to the next job with
no restart and no rebuild.

Two exceptions are marked in the UI: **speech model** and **precision** are
bound when the model loads, so changing them there is saved immediately and
takes effect the next time the container starts. Settings written in the UI
land in `/config/settings.json` and are layered over `.env` at startup, so a
value set in the UI wins over the same variable in `.env` from then on.

| Variable | Do this if… |
|---|---|
| `MODEL_SIZE=distil-large-v3` | You want ~2× throughput and can accept slightly worse rare-word accuracy |
| `MODEL_SIZE=medium.en` | Your dubs are clean and you want speed |
| `COMPUTE_TYPE=int8_float16` | You're tight on VRAM (roughly halves it) |
| `CONCURRENCY=2` | You have two *separate* GPUs. Never on a single card |
| `MAX_CHARS_PER_LINE=37` | You watch on a phone and lines wrap badly |
| `MAX_CPS=17` | Captions feel too fast to read |
| `OVERWRITE=true` | You're iterating on settings and don't want timestamped duplicates |

Rough throughput for a 24-minute episode at `large-v3` / `float16`:

| GPU | VRAM used | Time |
|---|---|---|
| RTX 3090 / 4090 | ~4.7 GB | 1.5-2 min |
| RTX 2070 Super / 2080 | ~4.7 GB of 8 GB | 4-7 min |
| CPU only | - | 30-60 min |

Turing cards (20-series) have working FP16 tensor cores, so `float16` is the
right setting there; the gap is raw throughput, not precision support.

---

## When it goes wrong

**`Could not load libcudnn_ops_infer.so`**: the classic. The `ctranslate2` pin
and the CUDA base image must agree on cuDNN major version. This image ships
cuDNN 9 and pins `ctranslate2==4.5.0` to match. If you change either, change
both.

**Captions don't match the audio at all**: wrong track. The UI shows which
stream was picked under the progress ribbon. Some releases mislabel their
language tags; check with `ffprobe -show_streams file.mkv`.

**Everything is offset by a constant few seconds**: some WEB-DL muxes start
audio after video. `audio_start_offset()` handles this, but if a file is still
off, it's likely a variable-rate issue that needs `ffsubsync` instead.

**Names still wrong**: the source had no text subtitle track to mine, so
there was no glossary. Check the job card: it lists the terms it found. Bitmap
subtitles (PGS/VobSub) can't be mined without OCR.

**`cuBLAS failed with status CUBLAS_STATUS_NOT_SUPPORTED`**: the precision
isn't supported on your card. Blackwell (RTX 50-series) has no int8 cuBLAS
path in this `ctranslate2` build, and the library advertises one anyway, so
`int8_float16` fails at the first matmul. Set precision to `float16` under
**Settings → Speech model** and restart. The app translates this error into
that instruction rather than surfacing the raw cuBLAS text.

**Nothing survived cleanup**: almost always the wrong audio track, or a track
that's pure music.

**Anything else**: hit **Show logs** in the top bar. It streams the same
output as `docker logs`, including `faster_whisper`'s own lines: which audio
track was chosen, and how much audio VAD removed. That second number is the
one to tune `VAD_THRESHOLD` against. If it's eating whole seconds of a
dialogue-heavy scene, lower the threshold.

---

## Layout

```
UNRAID.md              Unraid setup guide (start here on Unraid)
START-HERE.md          Windows / generic Docker setup guide
unraid-template.xml    optional Unraid Docker template
backend/app/
  main.py              FastAPI routes, SSE, static serving
  db.py                SQLite job store
  config.py            env-driven settings
  pipeline/
    probe.py           stream selection
    audio.py           ffmpeg extraction
    glossary.py        proper-noun mining from the sub track
    transcribe.py      faster-whisper
    clean.py           hallucination + repetition + name correction
    segment.py         cue building and line wrapping
    srt.py             SRT read/write
    runner.py          worker threads and orchestration
frontend/src/
  App.jsx              shell
  components/
    CaptionBay.jsx     live caption display + progress ribbon
    Library.jsx        file browser
    Queue.jsx          job list
    CueEditor.jsx      caption editor: replace-all, validation, split/merge
```

## Local development

```bash
# terminal 1
cd backend && pip install -r requirements.txt
DATA_DIR=./data MEDIA_ROOTS=/path/to/anime DEVICE=cpu MODEL_SIZE=base.en \
  uvicorn app.main:app --reload --port 8080

# terminal 2
cd frontend && npm install && npm run dev
```

Vite proxies `/api` to port 8080. Use `DEVICE=cpu` with a small model so you
aren't waiting on the GPU while iterating on UI.

## Note on use

This transcribes audio from files you already have, for your own playback. It
doesn't download anything or fetch subtitles from anywhere. Generated captions
are a derivative of the original dub script: fine for personal accessibility
use, worth thinking twice about redistributing.

---

## Support

- **Bugs and feature requests:** [Issues](https://github.com/wastedpadre/verbatim/issues)
- **Questions and setup help:** [Discussions](https://github.com/wastedpadre/verbatim/discussions).
  Start with the pinned *Start here* thread, which covers the requirements, the
  two things that trip almost everyone, and the current known issues.
- **Unraid forums:** [mrskc303](https://forums.unraid.net/profile/174026-mrskc303/)

Whichever you pick, the single most useful thing you can include is the log.
Hit **Show logs** in the top bar and paste the relevant lines, along with your
GPU, the model size and precision you're running, and which audio track the
job card says it chose.

---

## Licence and third-party components

The contents of this repository, the application code, the Unraid templates
and the docs, are **MIT** (see `LICENSE`).

The published container image is a separate matter, because it bundles
software under other licences:

| Component | Licence | How it's used |
|---|---|---|
| ffmpeg / ffprobe | GPL-2+ as built by Ubuntu | Separate executables, run via the command line |
| mkvtoolnix | GPL-2+ | Separate executable, run via the command line |
| tini | MIT | Init process, reaps ffmpeg subprocesses |
| `nvidia/cuda` base image | NVIDIA's licence terms for its container images | Base layer providing CUDA and cuDNN |
| Python packages | MIT, BSD and Apache-2.0 | faster-whisper and ctranslate2 are MIT |
| Whisper model weights | MIT | Downloaded to `/config` on first run, not baked into the image |

Ubuntu builds ffmpeg with `--enable-gpl`, so the binary that ships in the
image is GPL-2+ even though most of ffmpeg's own source is LGPL-2.1+. The
same applies to mkvtoolnix.

None of that changes the licence of the code here. Verbatim never links
against those tools; it runs them as separate processes and reads their
output, which is ordinary command-line use rather than the creation of a
derivative work. Nothing copyleft is linked into the Python application.

If you redistribute the image rather than pull it from GHCR, the GPL
obligations for those binaries travel with it. They are unmodified Ubuntu
packages, so pointing at Ubuntu's published source satisfies that.
