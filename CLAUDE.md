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
- **`src/spotify_mcp/spotify_api.py`** - OAuth client wrapper plus the Feb 2026 regime fallback; tools talk to `self.sp` directly except for the endpoints whose path differs between regimes
- **`src/spotify_mcp/spotify_types.py`** - TypedDicts for the Spotify response shapes the server consumes
- **`src/spotify_mcp/utils.py`** - Redirect-URI normalization, Spotify ID/URI coercion
- **`scripts/spotify_api_watch.py`** - Changelog probe behind the `/spotify-api-watch` skill

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
- Convert Spotify exceptions to MCP-compliant errors, and never drop Spotify's `reason`
- Include Args/Returns in all tool docstrings

### New tools need annotations
Every tool declares a title, a Spotify icon and behaviour hints; `tests/test_tool_metadata.py`
fails without them, and also if the README tool table drifts from the code.

Get `destructiveHint` right rather than safe-by-default: clients use it to decide what to
confirm with the user, so marking an additive tool destructive trains people to click through
the prompts that matter. Destructive means overwrites or deletes existing data
(`remove_saved_tracks`, `unfollow_playlist`, `reorder_playlist_tracks`), not merely "writes"
(`save_tracks`, `add_to_queue`).

Guidance that applies to the whole surface goes in `INSTRUCTIONS` at the top of
`fastmcp_server.py`, not into every tool description — it ships once per session instead of
once per tool, and descriptions are under a size budget.

## The test suite will lie to you

`uv run pytest` runs fully offline against mocked spotipy calls. The sibling project
[spotify-mcp-cloudflare](https://github.com/jamiew/spotify-mcp-cloudflare) had all 42 of its
tests green while **every one of its 24 tools was dead in production** — three separate bugs
sat exactly where the suite substitutes a fake: a bound-`fetch` bug the injected fake couldn't
see, a scope list no fake consults, and a fallback threshold no test drove with a real 400.

Adding tests against the same mocks would have raised coverage and caught none of it. **Green
tests are necessary, not sufficient.** After changing `spotify_api.py`, the scopes, or a tool's
request shape, verify with live MCP calls before reporting done. Prefer real fixtures to new
mocks; where a mock is unavoidable, treat everything behind it as untested.

Two traps when verifying live:
- **Scope changes need re-authentication.** Adding a scope to `SCOPES` does not upgrade the
  cached token — delete the cache and re-auth, then retest.
- **`with_fallback` caches per process.** A fallback fix doesn't take effect until the server
  restarts, so a retest against a running server can still show the old failure.

## Spotify API regimes

Feb 2026 split apps into a **full/legacy** and a **restricted** regime that serve different
paths for the same operation. `with_fallback` in `spotify_api.py` tries restricted first,
falls back to legacy, and caches the answer per endpoint family. Paths and bodies there are
ported from spotify-mcp-cloudflare, which verified them against the live API.

Fields the restricted regime strips (`followers`, `popularity`, `email`, `country`, `product`)
are optional on every Pydantic model — keep them that way, and never make a
stripped-in-restricted field required.

Run `/spotify-api-watch` to check for upstream changes and probe which regime we're on. It's
also the right reflex when a tool starts failing in a way that smells upstream: a sudden
400/403 on something that worked, missing fields, or shrunken result counts.

### Known upstream quirks, don't chase them
- Playlists read back as `public: true` even when created private. Our request body is correct;
  this is Spotify's reporting. Tell the user to confirm in the Spotify app.
- Quota is counted per **developer account** since July 2026, so 429s are deliberately excluded
  from spotipy's retry list (`RETRY_STATUS_CODES`) — retrying burns every app you own.

