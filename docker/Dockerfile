FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install build deps for cryptography wheels (mostly precompiled, but be safe)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY pixelrelay /app/pixelrelay

RUN pip install --upgrade pip \
    && pip install ".[gateway]"

EXPOSE 8000

# Default to SQLite at /data/pixelrelay.db; override DATABASE_URL for Postgres.
ENV DATABASE_URL="sqlite+aiosqlite:////data/pixelrelay.db" \
    PIXELRELAY_HOST=0.0.0.0 \
    PIXELRELAY_PORT=8000

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["python", "-m", "pixelrelay.gateway"]
