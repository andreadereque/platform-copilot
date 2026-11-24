# The API image: small, non-root, dependencies from the lock file only.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project      # cached layer: deps change rarely
COPY src ./src
RUN uv sync --locked --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app /app /app
ENV PATH="/app/.venv/bin:$PATH" PORT=8080
USER 10001
EXPOSE 8080
# Cloud Run sets PORT; the app reads everything else (DATABASE_URL, LLM_PROVIDER...) from env.
CMD ["sh", "-c", "exec uvicorn platform_copilot.main:create_app --factory --host 0.0.0.0 --port ${PORT}"]
