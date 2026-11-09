# syntax=docker/dockerfile:1.7
# Dittobuffer runtime image: game server + admin console in one build.

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DITTOBUFFER_ROOT=/app

WORKDIR /app

# tzdata is required for the weather scheduler (Europe/Amsterdam default).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY protocol ./protocol
RUN pip install --no-cache-dir --no-deps -e .

COPY config ./config
COPY tools ./tools
COPY scripts ./scripts

# Runtime data, world snapshot and assets are mounted, never baked into the image.
RUN mkdir -p /app/data /app/assets /app/logs /app/run \
    && useradd --create-home --uid 10001 dittobuffer \
    && chown -R dittobuffer:dittobuffer /app
USER dittobuffer

EXPOSE 8445 8080 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import socket;s=socket.create_connection(('127.0.0.1',8445),3);s.close()" || exit 1

CMD ["python", "-m", "dittobuffer.runtime.http", "--config", "config/server.json"]
