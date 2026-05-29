# Changelog

## Unreleased
- new `reorder_playlist_tracks` tool: move a contiguous block of tracks to a new
  position within a playlist (zero-based positions, optional snapshot guard)

## 2026-05-29 — 0.3.1
- add the `mcp-name:` ownership marker to the README so the MCP Registry can
  verify the PyPI package (0.3.0 published to PyPI but failed registry validation)

## 2026-05-29 — 0.3.0

### Packaging & distribution
- published to PyPI as `spotify-mcp-jamiew` and to the MCP Registry as
  `io.github.jamiew/spotify-mcp` (`spotify-mcp` is taken by upstream)
- one-command install via `uvx spotify-mcp-jamiew`; README now has copy-paste
  setup for Claude Code, Claude Desktop, and Codex CLI
- automated release: a GitHub Actions workflow publishes to PyPI + the MCP
  Registry on GitHub release via OIDC trusted publishing (no stored tokens)
- added packaging tests that keep `server.json` in sync with `pyproject.toml`
  and the env vars the server reads

## 2026-05-28

### Modern MCP protocol features
- structured output: every tool returns a typed Pydantic model (real output
  schemas) instead of bare dicts
- tool annotations (readOnly/destructive/idempotent/openWorld hints) + titles,
  plus a Spotify icon on every tool, resource, and prompt
- progress + log notifications while paginating large playlists
- elicitation: `remove_tracks_from_playlist` confirms before deleting on clients
  that support it, and proceeds without prompting on clients that don't
- new resources: track / playlist / artist / album by id

### Fixes
- playback now reads from `current_playback()`, so device/volume/shuffle/repeat
  are populated instead of always null
- a destructive removal no longer slips through when an elicitation prompt errors
  on a capable client — only genuinely unsupported clients skip confirmation
- playlist add/remove now surface the returned `snapshot_id`
- search tolerates null entries in result items
- dropped unused error codes left over from the deleted error helpers

### Dependencies updated to current majors
- bumped the runtime + dev stack to latest: mcp 1.27, spotipy 2.26, pytest 9,
  mypy 2.0, ruff 0.15, pytest-cov 7 (pydantic 2.13 pulled in transitively)

### Audio-features and recommendations tools removed
- dropped both tools — Spotify deprecated those endpoints in nov 2024 and they
  return 403 for apps created after that (tool count 13 → 11)

### Dead code removed
- removed the unused Client wrapper, utils parsers, and error/logging helpers
  left over from the FastMCP rewrite (~1400 lines)

### Test coverage raised to 95%
- every tool now has a success and a failure test; the resources and prompts
  are covered too (was 55%)

### Typed Spotify response shapes
- added TypedDicts for the Spotify objects the server consumes, applied at the
  parse and model-building boundaries

## 2025-12-08 — 0.2.0
- batch support for tracks/audio features plus new tools
- added the release.sh helper script
