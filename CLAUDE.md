# CLAUDE.md

This file provides essential guidance for working with the Spotify MCP server codebase.

## Essential Commands

### Development
- `uv run spotify-mcp` - Start the MCP server
- `uv sync` - Sync dependencies 
- `uv run pytest` - Run all tests (must pass before commits)
- `uv run mypy src/` - Type checking (must pass before commits)

### Environment Setup
Required environment variables:
- `SPOTIFY_CLIENT_ID` - Spotify API Client ID
- `SPOTIFY_CLIENT_SECRET` - Spotify API Client Secret

Three-tier configuration (highest priority first):
1. Environment variables (for production/MCP usage)
2. `.env` file (for local development)
3. `pyproject.toml` defaults (fallback - edit `[tool.spotify-mcp.env]` section)

### Git Workflow
**Quality Gates**: Before any commit, ALWAYS run:
- `uv run mypy src/` - Type checking must pass
- `uv run pytest` - All tests must pass

**Commit Message Format:**
```
Brief description of change

Detailed explanation of what and why.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

## Architecture

FastMCP-based MCP server for Spotify Web API integration using Python/`uv`.

### Core Files
- **`src/spotify_mcp/fastmcp_server.py`** - Main MCP server with 13 tools using `@mcp.tool()` decorators
- **`src/spotify_mcp/spotify_api.py`** - Spotify API wrapper with OAuth authentication  
- **`src/spotify_mcp/utils.py`** - Data parsing and validation utilities

### Key Features
- **13 MCP Tools**: Playback control, search, queue management, playlist operations, track/artist info
- **Pagination Support**: Handles large datasets (10k+ tracks) with `limit`/`offset` parameters
- **OAuth Flow**: Automatic token management via spotipy
- **Type Safety**: Full Pydantic validation and MyPy compliance

## Development Guidelines

### Tool Design Principles
- **Single Responsibility**: One focused purpose per tool (avoid `action` parameters)
- **Consistent Returns**: Always return structured `Dict[str, Any]` 
- **Pagination-First**: Add `limit`/`offset` to tools that can return >20 items
- **Type Safety**: Use strict type hints and Pydantic validation

### Code Quality Standards
- Run `mypy` and `pytest` before every commit
- Convert Spotify exceptions to MCP-compliant errors
- Include Args/Returns in all tool docstrings
- Mock external API calls in tests

