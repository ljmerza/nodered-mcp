# nodered-mcp as a standalone container, serving MCP over HTTP.
#
#   docker run -d -p 8080:8080 \
#     -e NODERED_URL=http://nodered:1880 \
#     -e NODERED_MCP_AUTH_TOKEN=change-me \
#     ghcr.io/ljmerza/nodered-mcp
#
# Dependencies come from uv.lock, so the image runs exactly what CI tested.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
COPY --from=build /app/.venv /app/.venv
RUN useradd --system --no-create-home nodered-mcp
USER nodered-mcp
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    NODERED_MCP_TRANSPORT=http \
    NODERED_MCP_HOST=0.0.0.0 \
    NODERED_MCP_PORT=8080
EXPOSE 8080
ENTRYPOINT ["nodered-mcp"]
