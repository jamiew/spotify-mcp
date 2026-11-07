# CLAUDE.md

This file provides essential guidance for working with the Spotify MCP server codebase.

## Essential Commands

### Development
- `uv run spotify-mcp` - Start the MCP server (local alias; the package publishes as `spotify-mcp-jamiew`, so end users run `uvx spotify-mcp-jamiew`)
- `.mcp.json` is the headless dev entry (`claude -p --mcp-config .mcp.json --strict-mcp-config`); `tests/test_integration.py` launches it as configured on both protocol eras
- `uv sync` - Sync dependencies
- `uv run pytest` - Run all tests (must pass before commits)
- `uv run mypy src/` - Type checking (must pass before commits)
  - Optional speedup on mypy 2.x: `uv run mypy src/ --num-workers 4` (parallel checking)
- `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` - Lint and format (CI gate)
- `uv run bandit -r src/` - Security lint (CI gate)

### Environment Setup
Required environment variables:
- `SPOTIFY_CLIENT_ID` - Spotify API Client ID
- `SPOTIFY_CLIENT_SECRET` - Spotify API Client Secret
- `SPOTIFY_REDIRECT_URI` - OAuth redirect URI registered in the Spotify app (`http://127.0.0.1:8888`)

Configuration comes from environment variables first, then a `.env` file for local
development. The server starts and lists tools without them; the first Spotify request
fails with an error naming the missing variables (`LazyOAuth` in `spotify_api.py`).

### Git Workflow
**Quality Gates**: Before any commit, ALWAYS run every check listed under Development
(pytest, mypy, ruff check and format, bandit). CI runs the same set.

**Commit Message Format:**
```
Brief description of change

Detailed explanation of what and why.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

### Releasing
Publishing is fully automated via OIDC trusted publishing — no tokens stored anywhere.

1. Bump `version` in `pyproject.toml` and both versions in `server.json` (`tests/test_packaging.py`
   fails if they differ), commit, and create a GitHub release for tag `vX.Y.Z`
   (the `/release` skill or `release.sh` does the tag + `gh release create`).
2. The `release: published` event triggers `.github/workflows/publish.yml`, which tests → checks the
   tag matches the package version → builds → publishes to **PyPI** (`pypa/gh-action-pypi-publish`,
   OIDC) → publishes `server.json` to the **MCP Registry** (`mcp-publisher login github-oidc`).
   A manual run of the workflow skips the PyPI upload if that version already exists, so it can
   retry a failed registry publish.

The package publishes to PyPI as `spotify-mcp-jamiew` and to the registry as `io.github.jamiew/spotify-mcp`.

One-time setup (already required before the first successful run):
- PyPI: register a trusted publisher for project `spotify-mcp-jamiew` → owner `jamiew`, repo
  `spotify-mcp`, workflow `publish.yml`.
- MCP Registry: the `io.github.jamiew/*` namespace is authenticated automatically via GitHub OIDC.

## Architecture

MCP server for Spotify Web API integration using Python/`uv`, built on the MCP Python SDK v2
`MCPServer` (formerly `FastMCP`; the module keeps its `fastmcp_server.py` name).

The Cloudflare sibling is the canonical development direction: a shared TypeScript core
with local stdio and hosted adapters. Python remains supported, not deprecated. The sibling's
merged `updates` PR added local PKCE login, output schemas, search offset, snapshot reorder guards,
bounded bulk playlist reads/progress and removal elicitation; do not assume it is deployed.
Python retains six resource declarations, five prompts and best-effort playback confirmation.
Tool names do not imply matching arguments, limits or result shapes; see the
[README comparison](README.md#moving-between-implementations). Preserve Python's distinctive
behavior until a deliberate consumer migration, not a wholesale rewrite.

Keep `mcp[cli]>=2.3,<3` and the direct Pydantic runtime dependency. CLI workflows remain supported.
Record compatibility work under Unreleased in CHANGELOG; do not bump/publish a release unless
requested.

### MCP SDK v2 rules
- **Two protocol eras.** 2025-11-25 clients (initialize handshake, including v1 SDKs) and
  2026-07-28 clients (no server-to-client requests) both connect. Test protocol changes on
  both: `Client(mcp, mode="legacy")` and `mode="auto"`, as `tests/test_protocol.py` does.
- **Ask the user through a resolver**, never `ctx.elicit()`: return `Elicit(...)` from an
  `Annotated[..., Resolve(fn)]` parameter. `ctx.elicit()` fails on 2026-era connections.
  A resolver's question must depend only on the tool arguments, since the call is retried.
- **Failures the model should read must be `ToolError`s.** MCPServer replaces any other
  exception's text with "Error executing tool <name>". Tools raise `ValueError` for bad
  arguments and let `SpotifyException` propagate; `log_tool_execution` re-raises both as
  `ModelVisibleError`. Resolvers sit outside that wrapper and raise `ModelVisibleError`
  themselves.
- **Resources return Pydantic models and fail with `ResourceError`.** The SDK serializes
  the model as JSON; `spotify_resource` turns a `SpotifyException` into `ResourceError`
  (`ResourceNotFoundError` for a 404). Never return an error as content.
- **No `ctx.info()`/`ctx.log()`** (deprecated by SEP-2577). Use `logging`, or
  `ctx.report_progress(..., message=...)` for what the client should see.
- **Sync tools run on a worker thread**, so they don't block the event loop and can
  overlap. Prefer sync tools. Only `get_playlist_tracks` is async, for progress
  notifications; it runs spotipy calls through `asyncio.to_thread`.

### Core Files
- **`src/spotify_mcp/fastmcp_server.py`** - Main MCP server: tools, resources, and prompts using `@mcp.tool()`/`@mcp.resource()`/`@mcp.prompt()` decorators on an `MCPServer`, with typed Pydantic output models
- **`src/spotify_mcp/spotify_api.py`** - `create_client()` plus the Feb 2026 regime fallback; tools call the spotipy client directly except for the endpoints whose path differs between regimes
- **`src/spotify_mcp/errors.py`** - Classifies `SpotifyException`s into model-facing messages that keep Spotify's `reason`
- **`src/spotify_mcp/logging_utils.py`** - `log_tool_execution`, the decorator every tool wears: timing logs and error conversion
- **`src/spotify_mcp/spotify_types.py`** - TypedDicts for the Spotify response shapes the server consumes
- **`src/spotify_mcp/utils.py`** - Redirect-URI normalization, Spotify ID/URI coercion
- **`scripts/spotify_api_watch.py`** - Changelog probe behind the `/spotify-api-watch` skill

### Key Features
- **MCP Tools**: Playback control, search, queue management, playlist operations, track/artist/album info, and library tools for liked songs, albums, followed artists, podcasts and audiobooks
- **Structured Output**: Every tool returns a typed Pydantic model (real output schema)
- **Tool Annotations**: read-only/destructive hints and titles on every tool; the Spotify glyph ships once, on the server
- **Progress & Elicitation**: progress notifications for large paginations; confirmation before destructive playlist removals via an `Elicit` resolver (when the client declares form elicitation)
- **Pagination Support**: `limit`/`offset` on list tools; `get_playlist_tracks` reads at most 1000 tracks per call and returns `next_offset` and `truncated`
- **OAuth Flow**: Automatic token management via spotipy
- **Type Safety**: Full Pydantic validation and MyPy compliance
- **Logging**: One completion line per tool call, with timing, on stderr; the SDK logs failures

## Development Guidelines

### Tool Design Principles
- **Single Responsibility**: One focused purpose per tool (avoid `action` parameters)
- **Structured Returns**: Return a typed Pydantic model so the tool has a real output schema
- **Pagination-First**: Add `limit`/`offset` to tools that can return >20 items
- **Type Safety**: Use strict type hints and Pydantic validation

### Code Quality Standards
- Run the quality gates above before every commit
- Let `SpotifyException` reach `log_tool_execution` rather than catching it per tool, and never drop Spotify's `reason`
- Tool docstrings say what the schema can't: ranges, accepted values, behavior. Don't restate types, defaults or the output model

### New tools need annotations
Every tool declares a title and behaviour hints; `tests/test_tool_metadata.py`
fails without them, if the README tool table drifts from the code, and if the
tools/list payload outgrows its budget. Don't add per-tool icons: an inline icon
on every tool was over a quarter of that payload.

Get `destructiveHint` right rather than safe-by-default: clients use it to decide what to
confirm with the user, so marking an additive tool destructive trains people to click through
the prompts that matter. Destructive means overwrites or deletes existing data
(`remove_saved_tracks`, `unfollow_playlist`, `reorder_playlist`), not merely "writes"
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
- **Scope changes need reauthorization.** Adding `user-follow-read` to `SCOPES` does not
  upgrade an existing grant. Restart and complete Spotify consent, then retest. Never delete
  the auth cache automatically, read its contents into logs, or share it.
- **`with_fallback` caches per process.** A fallback fix doesn't take effect until the server
  restarts, so a retest against a running server can still show the old failure.

## Spotify API regimes

Feb 2026 split apps into a **full/legacy** and a **restricted** regime that serve different
paths for the same operation. `with_fallback` in `spotify_api.py` tries restricted first,
falls back to legacy, and caches the answer per endpoint family. Paths and bodies there are
ported from spotify-mcp-cloudflare. Keep request-level regression coverage for both shapes.

The [March 9 announcement update](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
postponed endpoint restrictions for existing integrations; Extended Quota Mode is exempt
from February's endpoint restrictions. Do not infer universal withdrawal from a date or 403.
The [March changelog](https://developer.spotify.com/documentation/web-api/references/changes/march-2026)
reversed external-ID removal. Preserve IDs when present.

Membership and track writes retain public 50-item caps (albums 20), with 40-item upstream
chunks. Artist membership uses `/me/library/contains` URIs and a legacy follow-route fallback;
both need `user-follow-read`. Validate every membership chunk before concatenation. Failed
writes must propagate, never return success after only some chunks; earlier chunks cannot be
rolled back automatically. New playlists are intentionally private by default.

The official TypeScript SDK's
[1.2.0 playlist code](https://unpkg.com/@spotify/web-api-ts-sdk@1.2.0/dist/mjs/endpoints/PlaylistsEndpoints.js)
still uses legacy routes; “official SDK” is not a reason to remove compatibility logic.
Spotipy is community-maintained.

Search retries an oversized page at 10 only for an invalid-limit response. Batch track reads
fall back to individual requests on 403. Both cache only successful fallbacks. Spotipy prefixes
error messages with the request URL: do not mistake its `limit=` parameter for a limit error.
Artist top tracks and playlist counts are optional enrichment on 403, not on authentication
or rate-limit failures. Spotify applies player commands asynchronously, so `control_playback`
confirmation is best effort and returns the last observation if a later read fails.

Fields the restricted regime strips (`followers`, `popularity`, `email`, `country`, `product`)
are optional on every Pydantic model — keep them that way, and never make a
stripped-in-restricted field required.

Run `/spotify-api-watch` to check for upstream changes and probe which regime we're on. It's
also the right reflex when a tool starts failing in a way that smells upstream: a sudden
400/403 on something that worked, missing fields, or shrunken result counts.

### Quotas, sharing and policy
- The [July 23 quota announcement](https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates)
  allows 25 apps but shares the Development Mode quota per developer account. Preserve
  `QUOTA_EXCEEDED`; Python intentionally does not retry 429. Ordinary rate limits retain
  `Retry-After`. No guaranteed daily/24-hour quota reset is documented.
- [Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
  normally limit Development Mode to five allowlisted users with a Premium owner; existing
  larger allowlists may be grandfathered. Extended access currently requires organizations,
  an established entity, launched service and at least 250,000 MAU. A hosted URL is not
  permission for public enrollment; never share Python's single-user token cache.
- [Developer Policy](https://developer.spotify.com/policy) III.14 restricts AI ingestion,
  not just training; metadata-only MCP is not automatic clearance. III.13 covers analysis
  and derived metrics; III.3 covers voice-control assistants. Resolve policy permission
  before public AI access. Do not market compatibility work as Spotify approval.
- If playlist visibility reads back unexpectedly, verify it in the Spotify app rather than
  assuming the write succeeded or making a universal claim about Spotify's reporting.

