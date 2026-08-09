# Start here

> **Running this on Unraid?** Read `UNRAID.md` instead: Unraid handles Docker
> and GPU passthrough differently enough that following this guide will leave
> you on CPU without any obvious error. This file covers Windows and generic
> Docker.

You've never built a Docker container. That's fine; you won't really be
"building" one so much as running a single command that reads a recipe. This
guide assumes zero Docker background and tells you exactly what to type.

Total time: about 10 minutes of your attention, plus a wait while it downloads.

---

## 1. Keep the folder structure intact

This is the one thing that will break everything if you get it wrong.

The 28 files only work in their exact folders. Unzip the archive **as a
folder**: don't drag the files out into a single directory. When you're done
you should see this, with `Dockerfile` sitting next to a `backend` folder and
a `frontend` folder:

```
verbatim/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── README.md
├── backend/
│   ├── requirements.txt
│   └── app/ ...
└── frontend/
    ├── package.json
    └── src/ ...
```

If `Dockerfile` and `main.py` are in the same folder, it's wrong. Re-extract.

> **Note on `.env.example`:** files starting with a dot are hidden by default.
> On macOS press `Cmd+Shift+.` in Finder to reveal them. On Windows, enable
> "Hidden items" in File Explorer's View tab. The file is there.

---

## 2. Get Docker running with GPU access

Pick the machine that has the GPU in it.

### If that's your Unraid box

1. **Apps** tab → search **Nvidia Driver** → install it → reboot.
2. **Apps** tab → search **Docker Compose Manager** → install it.
3. Copy the `verbatim` folder to your server, e.g. to
   `/mnt/user/appdata/verbatim-src/`. The easiest way is over SMB; it'll
   show up as a network share.

### If that's a Windows workstation

1. Install **Docker Desktop** from docker.com. It will prompt you to enable
   WSL2; say yes and let it reboot.
2. Update your NVIDIA driver to anything recent. GPU passthrough to WSL2
   works out of the box on current drivers; there's nothing extra to install.
3. Open **PowerShell** and `cd` into the `verbatim` folder.

Verify Docker can see your GPU before going further:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

You should see a table listing your card. **If this fails, stop and fix it
here**: nothing downstream will work, and every error you'd get later would
be a confusing symptom of this one problem.

---

## 3. Point it at your anime

Two small edits.

**First,** copy `.env.example` to a new file named exactly `.env`:

```bash
cp .env.example .env
```

On Windows PowerShell: `Copy-Item .env.example .env`

You don't need to change anything inside it yet. The defaults are sensible.

**Second,** open `docker-compose.yml` in any text editor and look at these
two lines:

```yaml
      - /mnt/user/media/anime:/media
      - /mnt/user/appdata/verbatim:/config
```

The part **before** each colon is a folder on your machine. The part **after**
is what the container calls it. Only change the left side.

- Unraid: `/mnt/user/media/anime` is probably already right. Adjust if your
  share is named differently.
- Windows: use forward slashes, like `C:/Users/YourName/Videos/Anime:/media`
  and `C:/Users/YourName/verbatim-config:/config`.

`/config` is where it keeps the downloaded model and its job database. Point
it somewhere with a few GB free that won't get wiped.

---

## 4. Run it

From inside the `verbatim` folder:

```bash
docker compose up -d --build
```

Here's what that actually does, since you asked:

- `build` reads the `Dockerfile` and assembles an image: it downloads a base
  Linux image with CUDA, installs Python, ffmpeg, and the speech library, then
  compiles the React UI into static files. **This takes 5-15 minutes the first
  time** and produces a lot of scrolling text. That's normal.
- `up` starts a container from that image.
- `-d` means detached: it keeps running after you close the terminal.

You only pay the build cost once. Later starts take about two seconds.

---

## 5. Open it

Go to `http://localhost:8080`, or `http://YOUR-SERVER-IP:8080` from another
machine on your network.

Browse to a folder, tick an episode, hit **Caption episodes**.

**The first job is slow**: it downloads the speech model, about 3 GB, before
it starts. The progress bar will sit near zero for several minutes. Every job
after that starts instantly because the model is cached in `/config`.

Watch the caption bay at the top. Once words start appearing, check they
match what's being said in the dub. If they're nonsense, it picked the
Japanese track; see Troubleshooting in `README.md`.

When it finishes, the `.srt` lands next to your video file.

Jellyfin turns it on by itself. **Plex will not**: start the episode, open the
subtitle menu, and pick **English (SRT External)**. That's a per-series default
you can set once under *Settings → Subtitles*; until you do, Plex plays with
subtitles off even though the file is right there.

---

## The four commands you'll actually use

```bash
docker compose logs -f      # watch what it's doing (Ctrl+C to stop watching)
docker compose restart      # restart it
docker compose down         # stop it
docker compose up -d --build   # rebuild after you change any code
```

Run these from inside the `verbatim` folder or they won't know what you mean.

---

## When something goes wrong

**`docker: command not found`**: Docker isn't installed, or on Windows,
Docker Desktop isn't running. Start it and wait for the whale icon to go
steady.

**`no such file or directory` during build**: the folder structure got
flattened. Go back to step 1.

**The Library pane is empty**: the left side of your `/media` volume line
doesn't point at real files. Check the path, then `docker compose restart`.

**Anything mentioning `libcudnn`**: GPU libraries aren't matching. Re-run the
`nvidia-smi` check in step 2.

**It's using CPU and taking forever**: the `--gpus` request silently failed.
On Unraid make sure `--runtime=nvidia` is set; the `nvidia-smi` test in step 2
is the real diagnostic.

For anything else, `README.md` has a fuller troubleshooting section.

---

## What's been tested and what hasn't

Worth being straight with you about this.

**Verified:** the caption shaping logic (1,500 randomized cases, 8,226 cues,
no rule violations), all API endpoints including validation and the editor's
save path (15 assertions), the timecode parser (2,000 round-trips), and the
frontend production build.

Also verified since: the Docker build, both locally and in CI; a clean pull of
the published image onto an empty `/config`, which downloaded the model and
transcribed end to end; the word repair pass against live Gemini, OpenAI and
Anthropic keys; and, most importantly, real anime on a real Unraid box, which
is what the whole thing is for.

**Expectations:** it works well. Not perfect, but a long way ahead of the
nothing you had before, which is the honest comparison for dubtitles. Budget
for fixing the odd name, and use the caption editor's find-and-replace when
one is wrong throughout. If something does go wrong, hit **Show logs** in the
top bar and start there.
