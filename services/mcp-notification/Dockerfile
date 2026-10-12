# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Layer 1: third-party dependencies only (cached unless uv.lock changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    --mount=type=bind,source=services,target=services \
    uv sync --frozen --no-install-workspace --no-editable --package opstriage-notification
# Layer 2: workspace code (ADR 0001: the whole services/ tree, so the workspace lock validates)
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --package opstriage-notification

FROM python:3.13-slim
# OPSTRIAGE_ENV: example (local) | dev | prod. Selects the baked-in config files.
ARG OPSTRIAGE_ENV=example
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8101
CMD ["opstriage-notification"]
