---
name: spotify-api-watch
description: Check for Spotify Web API changes and verify this server still matches them. Use when the user says "check the Spotify API", "any Spotify API changes", "/spotify-api-watch", before a release, or on a schedule. Also use when a Spotify tool starts failing in a way that smells like an upstream change (sudden 403/400 on a tool that used to work, missing response fields, shrunken result counts).
---

# Spotify API watch

Spotify ships breaking changes to the Web API with little notice and no RSS feed.
This skill answers two questions: **what did Spotify change**, and **does our server
still work**. Do both — a clean changelog does not mean nothing broke, because the
regime flip described below happens silently.

## 1. Changelog sweep

```sh
uv run scripts/spotify_api_watch.py
```

Exit 1 means there are unreviewed entries. Spotify publishes no feed and no
changelog index, so this probes the predictable per-month URLs
(`.../references/changes/<month>-<year>`) and diffs against
`scripts/spotify-api-seen.json`.

For each `NEW` URL, fetch it and classify every item as:

- **Breaks us** — an endpoint spotipy calls, a field one of the Pydantic models in
  `src/spotify_mcp/fastmcp_server.py` requires, or a path in the fallback layer at
  the bottom of `src/spotify_mcp/spotify_api.py`.
- **Unlocks something** — new capability worth a tool or a scope.
- **Irrelevant** — dashboard/quota/billing with no code impact.

Then re-run with `--accept` to record them as reviewed, and commit the updated JSON.
Only accept after you have actually read the entries.

## 2. Live conformance probe

The changelog tells you what Spotify announced; this tells you what our app actually
gets. Requires the server connected as an MCP client — ask the user to reconnect if
the tools are absent.

Run these and compare against the expectations:

| Call | Full/legacy regime | Restricted regime |
| --- | --- | --- |
| `get_me` | returns `email`, `country`, `product` | id only |
| `get_artist_info` on any artist | has `followers`, `popularity` | both absent |
| `search_music` with `limit=20` | can return >10 | capped at 10 |
| `save_tracks` on one id | succeeds via legacy `/me/tracks` | succeeds via `/me/library` |

**If the probe shows a flip to restricted**, expect these to matter:

- `search_music` max drops 50 → 10 (our schema still advertises 50).
- Artist `followers`/`popularity` and user `email`/`country`/`product` vanish. Those
  fields are all optional on the Pydantic models, so they degrade rather than throw —
  keep them that way.
- Playlist and library writes move to `/items` and `/me/library`. `with_fallback` in
  `spotify_api.py` handles this, but it caches per process, so the first call after a
  flip may fail before it settles. Watch for the "resolved to the legacy regime" log
  line to see which way it went.

## 3. Report

Lead with whether anything is broken or newly possible. If nothing changed, say so in
one line — do not pad. When something did change, give the user a numbered list of
concrete options (fix X, adopt Y, ignore Z) with a recommendation.

Update the tracking section in `README.md` and `CLAUDE.md` if a change alters what this
server supports.

## Running it on a schedule

Steps 1 and 3 need no auth and are safe to automate. Step 2 needs a live MCP
connection, so a scheduled run should skip it. To alert rather than auto-change
anything, run the sweep and let the nonzero exit drive the notification — do not pass
`--accept` unattended, since that marks entries reviewed without anyone reading them.

## Sources

- Changelog — `https://developer.spotify.com/documentation/web-api/references/changes/<month>-<year>` (no index page; probe by URL)
- [Feb 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) — development vs extended, and what each loses
- [Scopes](https://developer.spotify.com/documentation/web-api/concepts/scopes)
- [Developer community forum](https://community.spotify.com/t5/Spotify-for-Developers/bd-p/Spotify_Developer) — where undocumented breakage surfaces first, usually days before the changelog
- [spotipy issues](https://github.com/spotipy-dev/spotipy/issues) — we depend on it, so breakage lands there early
- [spotify-mcp-cloudflare](https://github.com/jamiew/spotify-mcp-cloudflare) — the sibling server; its `withFallback` is the reference for endpoint shapes
