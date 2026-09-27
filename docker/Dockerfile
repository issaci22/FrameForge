# syntax=docker/dockerfile:1.7
# FrameForge: one image for every role (FF_ROLE=all | server | node).

# ---------------------------------------------------------------------------
# 1) Web UI build (Node.js only exists in this stage)
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# 2) Runtime: Python + jellyfin-ffmpeg (NVENC/NVDEC, QSV, VA-API, x264/x265/SVT-AV1/Opus)
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/shared:/app/server:/app/node \
    FF_CONFIG_DIR=/config \
    FF_WEB_DIR=/app/web/dist \
    FF_ROLE=all \
    FF_PORT=8686 \
    PUID=1000 \
    PGID=1000 \
    TZ=Etc/UTC \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg gosu tini tzdata; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key | gpg --dearmor -o /etc/apt/keyrings/jellyfin.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/jellyfin.gpg] https://repo.jellyfin.org/debian bookworm main" > /etc/apt/sources.list.d/jellyfin.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends jellyfin-ffmpeg7; \
    ln -sf /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg; \
    ln -sf /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe; \
    if [ -x /usr/lib/jellyfin-ffmpeg/vainfo ]; then ln -sf /usr/lib/jellyfin-ffmpeg/vainfo /usr/local/bin/vainfo; fi; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
ARG FF_DEV=0
RUN pip install -r requirements.txt && if [ "$FF_DEV" = "1" ]; then pip install -r requirements-dev.txt; fi

COPY shared/ /app/shared/
COPY server/ /app/server/
COPY node/ /app/node/
COPY --from=web /web/dist /app/web/dist
COPY docker/entrypoint.sh /usr/local/bin/frameforge-entrypoint
RUN chmod +x /usr/local/bin/frameforge-entrypoint && mkdir -p /config

EXPOSE 8686
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD [ "$FF_ROLE" = "node" ] || curl -fsS "http://127.0.0.1:${FF_PORT}/healthz" >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/frameforge-entrypoint"]
