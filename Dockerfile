# Multi-stage build for Spotify MCP server
FROM python:3.12-alpine AS builder

# Install build dependencies
RUN apk add --no-cache build-base

# Install uv
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY README.md ./

# Sync dependencies and build
RUN uv sync --frozen --no-dev
RUN uv build

# Production stage
FROM python:3.12-alpine AS runtime

# Install uv for runtime
RUN pip install --no-cache-dir uv

# Copy the built wheel and install it
COPY --from=builder dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Create non-root user for security
RUN addgroup -g 1001 -S spotify && \
    adduser -S -D -H -u 1001 -s /sbin/nologin -G spotify spotify

USER spotify

# Command to run the MCP server
CMD ["spotify-mcp"]
