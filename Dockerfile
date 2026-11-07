# Multi-stage build for Spotify MCP server
FROM python:3.12-alpine AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install the locked runtime dependencies and the package itself into /app/.venv
RUN uv sync --locked --no-dev --no-editable

# Production stage
FROM python:3.12-alpine AS runtime

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Create non-root user for security
RUN addgroup -g 1001 -S spotify && \
    adduser -S -D -H -u 1001 -s /sbin/nologin -G spotify spotify

USER spotify

# Command to run the MCP server
CMD ["spotify-mcp"]
