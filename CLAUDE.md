# CLAUDE.md

This file provides essential guidance for working with the Spotify MCP server codebase.

## Essential Commands

### Development
- `uv run spotify-mcp` - Start the MCP server (local alias; the package publishes as `spotify-mcp-jamiew`, so end users run `uvx spotify-mcp-jamiew`)
- `uv sync` - Sync dependencies 
- `uv run pytest` - Run all tests (must pass before commits)
- `uv run mypy src/` - Type checking (must pass before commits)
  - Optional speedup on mypy 2.x: `uv run mypy src/ --num-workers 4` (parallel checking)

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

### Releasing
Publishing is fully automated via OIDC trusted publishing — no tokens stored anywhere.

1. Bump `version` in `pyproject.toml`, commit, and create a GitHub release for tag `vX.Y.Z`
   (the `/release` skill or `release.sh` does the tag + `gh release create`).
2. The `release: published` event triggers `.github/workflows/publish.yml`, which tests → builds →
   publishes to **PyPI** (`pypa/gh-action-pypi-publish`, OIDC) → publishes to the **MCP Registry**
   (`mcp-publisher login github-oidc`), injecting the tag version into `server.json` at publish time.

The package publishes to PyPI as `spotify-mcp-jamiew` and to the registry as `io.github.jamiew/spotify-mcp`.

One-time setup (already required before the first successful run):
- PyPI: register a trusted publisher for project `spotify-mcp-jamiew` → owner `jamiew`, repo
  `spotify-mcp`, workflow `publish.yml`.
- MCP Registry: the `io.github.jamiew/*` namespace is authenticated automatically via GitHub OIDC.

## Architecture

FastMCP-based MCP server for Spotify Web API integration using Python/`uv`.

### Core Files
- **`src/spotify_mcp/fastmcp_server.py`** - Main MCP server: tools, resources, and prompts using `@mcp.tool()`/`@mcp.resource()`/`@mcp.prompt()` decorators, with typed Pydantic output models
- **`src/spotify_mcp/spotify_api.py`** - OAuth client wrapper (auth/token management only); tools talk to `self.sp` directly
- **`src/spotify_mcp/spotify_types.py`** - TypedDicts for the Spotify response shapes the server consumes
- **`src/spotify_mcp/utils.py`** - Redirect-URI normalization

### Key Features
- **MCP Tools**: Playback control, search, queue management, playlist operations, track/artist info
- **Structured Output**: Every tool returns a typed Pydantic model (real output schema)
- **Tool Annotations & Icons**: read-only/destructive hints, titles, and a Spotify glyph on tools/resources/prompts
- **Progress & Elicitation**: progress notifications for large paginations; confirmation prompts before destructive playlist removals (when the client supports it)
- **Pagination Support**: Handles large datasets (10k+ tracks) with `limit`/`offset` parameters
- **OAuth Flow**: Automatic token management via spotipy
- **Type Safety**: Full Pydantic validation and MyPy compliance
- **Performance Logging**: Comprehensive timing and debug logging for tools and API calls

## Development Guidelines

### Tool Design Principles
- **Single Responsibility**: One focused purpose per tool (avoid `action` parameters)
- **Structured Returns**: Return a typed Pydantic model so the tool has a real output schema
- **Pagination-First**: Add `limit`/`offset` to tools that can return >20 items
- **Type Safety**: Use strict type hints and Pydantic validation

### Code Quality Standards
- Run `mypy` and `pytest` before every commit
- Convert Spotify exceptions to MCP-compliant errors
- Include Args/Returns in all tool docstrings
- Mock external API calls in tests

