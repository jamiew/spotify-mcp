# Changelog

## 2026-05-28

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
