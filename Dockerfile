# ──────────────────────────────────────────────────────────────────────────────
# Steam Deal Telegram Bot — Dockerfile
# Multi-stage build for a small, secure production image.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

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
COPY --from=deps /usr/local/bin /usr/local/bin

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
    LOG_LEVEL=INFO

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "-u", "main.py"]
