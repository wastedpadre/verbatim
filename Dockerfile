# ---------------------------------------------------------------- frontend
# Node only exists at build time. The final image ships static files.
FROM node:20-alpine AS ui

WORKDIR /ui
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ----------------------------------------------------------------- runtime
# cuDNN 9 base — must stay in step with the ctranslate2 pin in requirements.txt.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/config/models

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 \
        python3-pip \
        ffmpeg \
        mkvtoolnix \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=ui /ui/dist ./app/static

VOLUME ["/media", "/config"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python3 -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

# tini reaps the ffmpeg subprocesses properly; without it a cancelled job can
# leave zombies behind.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
