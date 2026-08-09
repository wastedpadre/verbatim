# Running Verbatim on Unraid

Unraid does Docker differently enough from everywhere else that it's worth its
own guide. Follow this instead of `START-HERE.md`.

There are two ways to run it. **Path A (Compose)** is the one to use — it's
fewer steps and it's how you'll rebuild after any change. **Path B (Template)**
is optional, for getting it to appear in the Docker tab like a normal Unraid
app with a WebUI button.

---

## Prerequisites

**1. Nvidia Driver plugin.** Apps tab → search `Nvidia Driver` → install →
**reboot**. This is non-negotiable; without it Unraid can't hand the GPU to a
container at all.

After rebooting, go to **Settings → Nvidia Driver** and confirm your card is
listed. Note its UUID, which looks like this:

```
GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

The project ships `NVIDIA_VISIBLE_DEVICES=all`, which hands over every card and
needs no editing. To pin one card instead — worth doing if the box has a second
GPU you want left alone — put that UUID in `docker-compose.yml` and
`unraid-template.xml`.

**2. Compose Manager plugin.** Apps tab → search `Docker Compose Manager` →
install. No reboot needed.

**3. Verify GPU passthrough works.** Open the Unraid terminal (`>_` icon, top
right) and run:

```bash
docker run --rm --runtime=nvidia --gpus all \
  nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

You should get a table listing your cards. **If this fails, stop here.** Every
later problem would just be a confusing symptom of this one.

---

## Path A — Compose (recommended)

### 1. Get the source onto the server

Unraid has to build the image locally, so the files need to live on the array.
Put them somewhere that isn't your appdata folder for the running container:

```
/mnt/user/appdata/verbatim-src/
```

Easiest way: Unraid exposes shares over SMB, so open `\\TOWER\appdata` in
Explorer (or `smb://tower/appdata` in Finder) and drop the unzipped `verbatim`
folder in, renamed to `verbatim-src`.

Confirm the structure survived the copy — open the Unraid terminal:

```bash
ls /mnt/user/appdata/verbatim-src
```

You want to see `Dockerfile`, `docker-compose.yml`, `backend`, `frontend`. If
you see `main.py` at that level, the folders got flattened; re-copy.

### 2. Create the .env file

```bash
cd /mnt/user/appdata/verbatim-src
cp .env.example .env
```

The defaults are fine. Don't edit it yet.

### 3. Check your paths

Open `docker-compose.yml` and look at the `volumes:` section. The left side of
each colon is a real path on your server:

```yaml
      - /mnt/user/media/anime:/media
      - /mnt/user/appdata/verbatim:/config
```

Change the left sides to match your shares. Verify they exist first:

```bash
ls /mnt/user/media/anime
```

**If you use the TRaSH / hardlink layout** (`/mnt/user/data/media/...`), do
this instead — mount the single data root at the same container path Sonarr
uses:

```yaml
      - /mnt/user/data:/data
      - /mnt/user/appdata/verbatim:/config
```

and set `MEDIA_ROOTS=/data/media/anime` in `.env`. This is what makes the
Sonarr webhook work without path translation, and it's worth doing properly
now rather than debugging it later.

### 4. Add the stack

**Docker tab → Compose → Add New Stack.** Name it `verbatim`.

Click the gear next to it → **Edit Stack** → **Compose File**. Rather than
pasting the file contents, point it at the directory you created — Compose
Manager has a field for the stack directory. Set it to:

```
/mnt/user/appdata/verbatim-src
```

Then **Compose Up**. The first build takes 5–15 minutes and produces a lot of
scrolling output; that's normal. It's downloading a CUDA base image,
installing Python and ffmpeg, and compiling the React UI.

If you'd rather just watch it in the terminal:

```bash
cd /mnt/user/appdata/verbatim-src
docker compose up -d --build
```

### 5. Open it

`http://YOUR-TOWER-IP:8080`

---

## Path B — Unraid template (optional)

This gets Verbatim into the Docker tab with a proper WebUI button, editable
variables, and Unraid's normal start/stop controls.

**It does not replace Path A.** The template has no registry to pull from, so
the image must already exist locally. Build it first with Path A step 4, then:

```bash
cp /mnt/user/appdata/verbatim-src/unraid-template.xml \
   /boot/config/plugins/dockerMan/templates-user/my-verbatim.xml
```

**Docker tab → Add Container →** pick `verbatim` from the template dropdown at
the top. Check the paths, hit Apply.

If you go this route, stop the Compose stack first so you don't have two
containers fighting over port 8080.

---

## Unraid-specific gotchas

**Keep `/config` on a cache pool.** That's where the job database and the 3 GB
speech model live. Appdata defaults to cache, so this is usually automatic —
but if your appdata share is set to `Yes` for "Use cache pool" with mover
enabled, the model can get shuffled to the array and everything slows down.
Set that share to `Prefer` or `Only`.

**The array is slow for the model download.** First run pulls about 3 GB into
`/config/models`. The progress bar will sit near zero for several minutes.
Only happens once.

**Don't run this during a parity check** if you can help it. Transcription is
GPU-bound, not disk-bound, but reading a 4 GB remux off a degraded array while
parity is running will bottleneck the ffmpeg extraction step badly.

**Writes go to your media share.** Verbatim writes the `.srt` next to each
video, so the `/media` mount needs `rw` — it's set that way by default, but if
you tightened it to `ro`, jobs will fail at the very last step after doing all
the work.

**`NVIDIA_DRIVER_CAPABILITIES` is not optional here.** This trips people up
because the GPU parameters passed around in Unraid forums come from transcoding
containers (Plex, Jellyfin, Tdarr), which only need `video` and `utility`.
CUDA compute is a separate capability. If `compute` isn't in that list, the
container starts fine, reports no CUDA device, and falls back to CPU without
logging an error. Both `docker-compose.yml` and the template set
`compute,utility` already — just don't strip it when copying params around.

**Leave `CONCURRENCY` at 1.** Two jobs on one GPU contend for the same VRAM
and both run slower. On an 8 GB card it will also run out of memory outright.
Only raise it if you add a physically separate second GPU.

**Watch out for GPU contention with Plex.** If Plex, Jellyfin or Tdarr are
doing hardware transcoding on the same card, they and Verbatim are competing
for the same 8 GB. `large-v3` at `float16` holds roughly 4.7 GB for the whole
job, so a couple of simultaneous Plex transcodes can push the card into OOM
and fail the job mid-run. Two ways to avoid it:

- Set `COMPUTE_TYPE=int8_float16` in `.env` -- drops Verbatim to about
  2.6 GB, leaving real headroom. Small accuracy cost.
- Or caption when nothing else is using the GPU.

Check actual usage while a job runs:

```bash
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu \
  --format=csv -l 2
```

---

## Wiring up Sonarr

**Sonarr → Settings → Connect → Add → Webhook**

- URL: `http://YOUR-TOWER-IP:8080/api/webhook/sonarr`
- Method: POST
- Triggers: **On Import** and **On Upgrade**

Hit **Test** — you should get a green tick, and `docker compose logs` will
show the request arrive.

The catch worth understanding: Sonarr sends the file path *as Sonarr sees it*.
If Sonarr's container calls it `/tv/Show/Ep.mkv` and Verbatim's calls it
`/media/Show/Ep.mkv`, Verbatim can't find the file and the job fails
immediately. Mounting the same host path at the same container path in both
containers is the fix, which is why the TRaSH layout note above matters.

---

## The commands you'll actually use

Run these from `/mnt/user/appdata/verbatim-src`:

```bash
docker compose logs -f          # watch what it's doing (Ctrl+C to exit)
docker compose restart          # restart
docker compose down             # stop
docker compose up -d --build    # rebuild after changing code or .env
```

---

## Troubleshooting

**It runs but every episode takes 30+ minutes** — it's on CPU. The GPU request
failed silently. Check:

```bash
docker exec verbatim python3 -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"
```

`0` means no GPU. Re-run the `nvidia-smi` test from Prerequisites, and confirm
`runtime: nvidia` survived any edits to your compose file.

**`unknown or invalid runtime name: nvidia`** — the Nvidia Driver plugin isn't
installed, or you didn't reboot after installing it.

**Library pane is empty** — the left side of your `/media` volume line doesn't
point at real files, or `MEDIA_ROOTS` in `.env` doesn't match the container
path. Both have to agree.

**Captions don't match the audio at all** — it picked the Japanese track. The
UI shows which stream it chose under the progress ribbon. See the
Troubleshooting section in `README.md`.

**Anything mentioning `libcudnn`** — the CUDA base image and the ctranslate2
pin have drifted apart. If you didn't edit `Dockerfile` or `requirements.txt`,
this shouldn't happen; if you did, both must move together.
