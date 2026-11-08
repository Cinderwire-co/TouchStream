# TouchStream Hub - one container: web app + MediaMTX + FFmpeg
FROM python:3.13-slim

ARG MEDIAMTX_VERSION=1.21.3

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# MediaMTX from its GitHub release, checked against the published checksums
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in amd64|arm64) ;; *) echo "Unsupported architecture: $arch"; exit 1 ;; esac; \
    base="https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}"; \
    file="mediamtx_v${MEDIAMTX_VERSION}_linux_${arch}.tar.gz"; \
    cd /tmp; \
    curl -fsSLO "$base/$file"; \
    curl -fsSL "$base/checksums.sha256" | grep " \*$file\$" | sha256sum -c -; \
    tar -xzf "$file" -C /usr/local/bin mediamtx; \
    rm "$file"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY touchstream_hub ./touchstream_hub
RUN pip install --no-cache-dir '.[transcribe]'

ENV TOUCHSTREAM_DATA=/data \
    TOUCHSTREAM_INSTALL=docker \
    TOUCHSTREAM_MEDIAMTX=/usr/local/bin/mediamtx \
    PYTHONUNBUFFERED=1
VOLUME /data

# 8080 web dashboard/API, 8890/udp SRT from spokes, 1935 RTMP from other encoders
EXPOSE 8080 8890/udp 1935

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fs http://127.0.0.1:8080/api/state || exit 1

CMD ["touchstream-hub"]
