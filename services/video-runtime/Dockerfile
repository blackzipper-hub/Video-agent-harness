FROM python:3.11-slim AS base

# Optional regional build acceleration (opt-in; defaults to official sources, so OSS / non-China
# users are unaffected). Enable, e.g. from China:
#   docker build \
#     --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn \
#     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#     --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn .
# Debian 13/trixie keeps apt sources in the DEB822 file /etc/apt/sources.list.d/debian.sources.
ARG APT_MIRROR=""
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""

RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$APT_MIRROR|g; s|security.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libpq-dev \
        gcc \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        libcairo2 \
        fonts-droid-fallback \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN if [ -n "$PIP_INDEX_URL" ]; then pip config set global.index-url "$PIP_INDEX_URL"; fi && \
    if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi && \
    pip config set global.timeout 120 && \
    pip config set global.retries 8 && \
    pip install --no-cache-dir pip setuptools wheel && \
    (pip install --no-cache-dir ".[dev]" 2>/dev/null || pip install --no-cache-dir .)

COPY . .

# Make the top-level packages (app, prompts, ee) importable straight from source,
# independent of pip package discovery. app/ also holds app/chat and app/chat/prompts.
ENV PYTHONPATH=/app

RUN mkdir -p /workspace /tmp/cuti-workspace /app/data/run_workspaces /app/data/uploads

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
