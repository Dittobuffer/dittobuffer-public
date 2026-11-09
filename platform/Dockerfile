# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY run.py pyproject.toml README.md LICENSE ./
COPY dittobuffer_platform ./dittobuffer_platform

RUN useradd --create-home --uid 10001 platform \
    && mkdir -p /data /workspace \
    && chown -R platform:platform /app /data /workspace
USER platform

VOLUME ["/data"]
EXPOSE 8780

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8780/api/health', timeout=3).read()" || exit 1

CMD ["python", "run.py", \
    "--config", "/data/config.json", \
    "--data", "/data/platform.json", \
    "--key", "/data/platform-key.json", \
    "--host", "0.0.0.0", \
    "--port", "8780", \
    "--server-root", "/workspace"]
