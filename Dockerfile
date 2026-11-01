# Multi-stage build for Spotify MCP server
FROM python:3.12-alpine AS builder

# Install uv
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Build the wheel
RUN uv build --wheel

# Production stage
FROM python:3.12-alpine AS runtime

# Copy the built wheel and install it
COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Create non-root user for security
RUN addgroup -g 1001 -S spotify && \
    adduser -S -D -H -u 1001 -s /sbin/nologin -G spotify spotify

USER spotify

# Command to run the MCP server
CMD ["spotify-mcp"]
