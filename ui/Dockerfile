# syntax=docker/dockerfile:1
# Agent Gateway demo UI: React frontend + FastAPI backend (agdemo_ui) in one Cloud Run image.
#
# Build context = repo root:
#   docker build -f ui/Dockerfile -t agdemo-ui .
#
# The image never contains config/demo.yaml or config/state.json (see .dockerignore). Provide them at runtime:
#   AGDEMO_CONFIG_YAML  full contents of config/demo.yaml  (env var, e.g. from a Secret Manager secret)
#   AGDEMO_STATE_JSON   full contents of config/state.json (env var or secret)
#   - or -
#   AGDEMO_CONFIG / AGDEMO_STATE  paths to mounted files (e.g. Cloud Run secret volumes at /secrets/...)
# Without them the UI still runs in Demo mode (live_available=false).
# Optional: AGDEMO_RECORDINGS_DIR (writable dir for Live "Record"; recordings in themes/ are read as fallback),
#           AGDEMO_LIVE_TIMEOUT (default 45 s), AGDEMO_PACE (demo pacing multiplier, default 1).
# Cloud Run sets K_SERVICE, which turns on the IAP admin check (X-Goog-Authenticated-User-Email vs ui.admin_access).

# ---------- 1. frontend ----------
FROM node:22-slim AS frontend
WORKDIR /src/ui/frontend
COPY ui/frontend/package.json ui/frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY ui/frontend/ ./
RUN npm run build

# ---------- 2. backend ----------
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PYTHONUNBUFFERED=1
WORKDIR /app

# dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# the repo package (installed editable so agdemo_core finds themes/ and config/ under /app)
COPY agdemo_core ./agdemo_core
COPY ui/backend ./ui/backend
COPY themes ./themes
COPY config/demo.example.yaml ./config/demo.example.yaml
RUN uv sync --frozen --no-dev

COPY --from=frontend /src/ui/frontend/dist ./ui/frontend/dist

ENV PATH="/app/.venv/bin:$PATH" PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn agdemo_ui.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
