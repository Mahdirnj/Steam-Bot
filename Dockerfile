# ──────────────────────────────────────────────────────────────────────────────
# Steam Deal Telegram Bot — Dockerfile
# Multi-stage build for a small, secure production image.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12.4-slim-bookworm AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Install dependencies (cached layer) ──────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Final image ──────────────────────────────────────────────────────────────
FROM base AS runtime

# Copy installed packages from the deps stage.
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Copy only the scripts we need (pip is not needed at runtime).
COPY --from=deps /usr/local/bin/python /usr/local/bin/python

# Copy application code.
COPY . .

# Create directories for persistent data (volume-mounted in docker-compose).
RUN mkdir -p /app/data /app/logs

# Non-root user for security.
RUN addgroup --system bot && adduser --system --ingroup bot bot \
    && chown -R bot:bot /app
USER bot

# Default env — overridden by docker-compose or .env file.
ENV DB_PATH=/app/data/bot.db \
    LOG_LEVEL=INFO \
    WEB_PORT=5000

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${WEB_PORT}/health')" || exit 1

CMD ["python", "-u", "main.py"]
