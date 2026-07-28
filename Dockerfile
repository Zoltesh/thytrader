# Local full-stack backend image for API, migration, and worker services.
FROM ghcr.io/astral-sh/uv:0.11.31 AS uv

FROM python:3.14-slim

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic ./alembic
COPY alembic.ini ./
COPY src ./src
RUN uv sync --locked --no-dev

CMD ["/app/.venv/bin/thytrader-api"]
