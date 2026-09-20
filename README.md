# spotify-mcp MCP server

mcp-name: io.github.jamiew/spotify-mcp

MCP server connecting Claude with Spotify. This fork of [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp) adds smart-batching tools and advanced playlist features that optimize API usage.

This is the **supported Python implementation**, running locally over stdio; it is not
deprecated. [spotify-mcp-cloudflare](https://github.com/jamiew/spotify-mcp-cloudflare) is the
canonical development direction: one TypeScript core for local stdio and hosted Workers.
Its sibling `updates` PR adds local PKCE login and shared tools; that is not a claim that
those changes are released or deployed. Existing Python clients can stay here.

## Features

### Core Functionality
- **Playback Control**: Start, pause, skip tracks, manage queue
- **Search & Discovery**: Find tracks, albums, artists, playlists with pagination  
- **Real-time State**: Live user profile and playback status
- **Resources**: Read user, playback, track, playlist, artist, and album state by URI

### Modern MCP Protocol
- **Server instructions**: whole-surface guidance ships once per session instead of per tool
- **Structured output**: every tool returns a typed schema, not a bare dict
- **Tool annotations & icons**: read-only/destructive hints, titles, and a Spotify glyph
- **Progress notifications**: live updates while paginating large playlists
- **Elicitation**: destructive playlist removals ask for confirmation on clients that support it

### Enhanced Playlist Tools (New in this fork)
- **Smart Batch Operations**: Add/remove up to 100 tracks in single API calls
- **Large Playlist Support**: Efficiently handle playlists with 1000+ tracks using pagination
- **Advanced Playlist Management**: Create, modify details, reorder tracks, bulk track operations
- **API-Optimized Workflows**: Intelligent batching reduces API calls by 60-80%

### Tools
| Tool | Does |
| --- | --- |
| `get_me` | The signed-in user's profile |
| `search_music` | Search tracks, albums, artists or playlists, with filters and regime-aware page limits |
| `get_tracks` | Details for up to 50 tracks in one request; individual reads when batching is unavailable |
| `get_artist` | Details for up to 50 artists in one request; top tracks when you ask for a single artist |
| `get_album` | Details for up to 20 albums in one request; the track list when you ask for a single album |
| `get_playback_state` | What's playing now: track, device, progress, shuffle, repeat |
| `control_playback` | Play, pause, next, previous, seek, volume, shuffle, repeat; best-effort state confirmation |
| `list_devices` | Available Spotify Connect devices |
| `transfer_playback` | Move playback to another device |
| `get_queue` | Now playing plus the upcoming queue |
| `add_to_queue` | Queue a track |
| `list_playlists` | The user's playlists, paginated |
| `get_playlist` | Playlist metadata without its tracks; track count when available |
| `get_playlist_tracks` | Playlist tracks, paginated to any size |
| `create_playlist` | Create a private playlist by default; pass `public=true` to publish |
| `update_playlist_details` | Rename a playlist or change its description/visibility |
| `add_tracks_to_playlist` | Add up to 100 tracks in one call |
| `remove_tracks_from_playlist` | Remove tracks (confirms first where the client supports it) |
| `reorder_playlist` | Move a block of tracks to a new position |
| `unfollow_playlist` | Unfollow a playlist — how Spotify deletes your own |
| `get_saved_tracks` | Liked Songs, paginated |
| `save_tracks` | Like tracks |
| `remove_saved_tracks` | Unlike tracks |
| `check_saved_tracks` | Which of up to 50 tracks are already liked, without paging the library |
| `check_saved_albums` | Which of up to 20 albums are already saved |
| `check_following_artists` | Which of up to 50 artists the user follows |
| `get_top_items` | Top artists or tracks over a time range |
| `get_recently_played` | Recently played tracks with timestamps |

`tests/test_tool_metadata.py` fails if this table drifts from the code, or if a tool ships
without a title, icon and behaviour annotations.

Restricted apps cap search pages at 10 results. Advance with the returned `offset + limit`,
not the requested page size. Individual track fallbacks can require up to 50 Spotify requests.
Playlist `limit`/`offset` count positions, so a page includes any unresolved rows at those
positions. Local files and unresolved rows come back with `id: null`, local files also set
`is_local`, and they keep their position so `reorder_playlist` indices stay correct.

**Intentional default change:** new playlists are now **private** unless you explicitly set
`public=true`, matching the Worker. Existing playlists are untouched. Spotify can report
unexpected visibility; confirm sensitive playlists in the Spotify app.

Track saves/removals and track/artist membership checks still accept up to **50** items per
tool call, split into upstream requests of at most **40**. Album checks keep their **20**-item
cap. Membership answers preserve input order before being keyed by ID; incomplete chunks
fail rather than shifting answers. Writes are not atomic across chunks: if a later request
fails, the tool reports an error, but earlier chunks may already have applied. Check membership
before retrying. Artist checks prefer `/me/library/contains` with artist URIs and fall back
to `/me/following/contains` for legacy apps.

### Moving between implementations

Matching versions and shared tool names are **not** drop-in compatibility. Review arguments,
limits and return shapes before switching a client:

| Area | Python | TypeScript sibling `updates` PR |
| --- | --- | --- |
| Local/hosted auth | Local stdio with Spotipy OAuth and one local cache | Local stdio with PKCE, or per-user hosted OAuth |
| Search | One type; explicit offset and filters; non-track results still use a track-like shape | Multi-type results with per-type shapes and starting offset |
| Playlist reads | Separate metadata and contents tools; fetch up to 10,000 entries with progress | Combined playlist tool; bounded fetch-all and progress |
| Playlist changes | Snapshot-guarded reorder; removal confirmation when supported | Adds snapshot guard and removal confirmation; also supports covers, insertion position and collaborative creation |
| Library | Track reads/writes; separate track, album and artist membership tools | Also saved-album and followed-artist reads/writes; consolidated membership tool |
| MCP | Typed output schemas, six resource declarations, five prompts | Adds declared output schemas; no resources, three prompts |
| Playback | Best-effort read-after-write confirmation | Mutation acknowledgement, not Python's confirmation behavior |
| Rate limiting | Surfaces all 429s without retrying | Distinguishes quota exhaustion from bounded ordinary rate-limit retries |

Python keeps its resources, prompts, output schemas, progress, removal elicitation, snapshot
guards and playback confirmation. Sibling PR features above describe source changes, not
production parity or a migration requirement.

## Spotify access, quotas and policy

Availability depends on the app's access regime. The
[March 9 update to Spotify's February announcement](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
**postponed endpoint restrictions for existing integrations**; Extended Quota Mode is exempt
from those February changes. Do not treat the migration guide's old blanket date as proof
that every older app lost an endpoint. Restricted apps use the
[February 2026 routes](https://developer.spotify.com/documentation/web-api/references/changes/february-2026):
playlist `/items`, creation at `/me/playlists`, and URI-based `/me/library` writes/checks.
They also have smaller search pages and restricted batch/playlist access. A 403 can mean
permissions, not endpoint retirement. The
[March changelog](https://developer.spotify.com/documentation/web-api/references/changes/march-2026)
reversed external-ID removal: external IDs remain available. The separate
[November 2024 restrictions](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
on recommendations, related artists and audio features/analysis were not universal withdrawal
for all approved apps.

The [July 23, 2026 quota update](https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates)
allows **25 Client IDs per developer account**, but all Development Mode apps on that account
share **one quota**. Separate local and hosted apps do not buy extra quota.
`error.reason: "QUOTA_EXCEEDED"` identifies quota exhaustion on HTTP 429. Python surfaces it
without automatic retries; ordinary rate limits expose `Retry-After` when provided.
Spotify does not specify a guaranteed quota reset time in that announcement; do not promise
a daily or 24-hour reset or rotate apps to evade limits.

Spotipy is community-maintained, not an official Python SDK. Spotify's official TypeScript SDK
[1.2.0 playlist implementation](https://unpkg.com/@spotify/web-api-ts-sdk@1.2.0/dist/mjs/endpoints/PlaylistsEndpoints.js)
still emits legacy `/tracks` and `/users/{id}/playlists` routes. Switching to an official SDK
does not replace the compatibility-aware endpoint layer.

### Sharing is not unrestricted public hosting

This Python server has one local Spotify identity; do not share its token cache or expose its
stdio process as an unauthenticated public service. Friends who self-host authorize their own
accounts. Hosted sharing belongs in the sibling Worker, with separate per-user OAuth and an
explicit allowlist, not a shared token.

[Development Mode](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
normally permits **five allowlisted users per app** and requires a Premium app owner; existing
larger allowlists can be grandfathered. Extended access is not routine hobby-project approval:
current criteria include an **organization**, an established legal entity, a launched service,
and **at least 250,000 monthly active users**. A public URL does not lift those restrictions.

**AI policy is a separate launch gate.** The [Spotify Developer Policy](https://developer.spotify.com/policy),
III.14, restricts training **or otherwise ingesting Spotify Content into an AI/ML model**.
“We do not train” and “only metadata” are not automatic clearance for MCP content sent to an
LLM. III.13 also restricts analysis and derived metrics; III.3 restricts voice-control assistants.
Seek Spotify clarification or approval before advertising public AI access. This is a policy
risk notice, not legal advice; community MCPs and Spotify's own AI partnerships grant no
permission to this project.

## Installation

Requires a Spotify **Premium** account and [`uv`](https://docs.astral.sh/uv/) >= 0.54.

### 1. Get Spotify API keys

1. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard).
2. Add redirect URI `http://127.0.0.1:8888` — it must match exactly what you set below.
3. Copy the **Client ID** and **Client Secret**.

### 2. Add the server to your MCP client

Every client runs the same command — `uvx spotify-mcp-jamiew` — with your three Spotify env vars. No clone, no local path.

**Standard config** (works in most clients):

```json
{
  "mcpServers": {
    "spotify": {
      "command": "uvx",
      "args": ["spotify-mcp-jamiew"],
      "env": {
        "SPOTIFY_CLIENT_ID": "your_client_id",
        "SPOTIFY_CLIENT_SECRET": "your_client_secret",
        "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888"
      }
    }
  }
}
```

<details>
<summary>Claude Code</summary>

```bash
claude mcp add spotify \
  -e SPOTIFY_CLIENT_ID=your_client_id \
  -e SPOTIFY_CLIENT_SECRET=your_client_secret \
  -e SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888 \
  -- uvx spotify-mcp-jamiew
```

Add `-s user` to install it globally across all projects. Verify with `claude mcp list`.
</details>

<details>
<summary>Claude Desktop</summary>

Add the **standard config** above to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows), then fully restart Claude Desktop.
</details>

<details>
<summary>Codex CLI</summary>

```bash
codex mcp add spotify \
  --env SPOTIFY_CLIENT_ID=your_client_id \
  --env SPOTIFY_CLIENT_SECRET=your_client_secret \
  --env SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888 \
  -- uvx spotify-mcp-jamiew
```

Or add to `~/.codex/config.toml`:

```toml
[mcp_servers.spotify]
command = "uvx"
args = ["spotify-mcp-jamiew"]

[mcp_servers.spotify.env]
SPOTIFY_CLIENT_ID = "your_client_id"
SPOTIFY_CLIENT_SECRET = "your_client_secret"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888"
```
</details>

<details>
<summary>Hermes</summary>

Add to `~/.hermes/config.yaml`, then run `/reload-mcp` (or restart Hermes):

```yaml
mcp_servers:
  spotify:
    command: uvx
    args: [spotify-mcp-jamiew]
    env:
      SPOTIFY_CLIENT_ID: your_client_id
      SPOTIFY_CLIENT_SECRET: your_client_secret
      SPOTIFY_REDIRECT_URI: http://127.0.0.1:8888
```
</details>

<details>
<summary>OpenClaw</summary>

Add the **standard config** above to `~/.openclaw/openclaw.json` (under `mcpServers`), then `openclaw gateway restart`.
</details>

<details>
<summary>Other clients (mcp.json)</summary>

Most MCP clients read a JSON file with an `mcpServers` block — drop the **standard config** above into it.

Using something else? Paste this to your agent:

> Install the spotify-mcp MCP server from https://github.com/jamiew/spotify-mcp — it's on PyPI as `spotify-mcp-jamiew`, run it with `uvx spotify-mcp-jamiew`, and set env vars `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, and `SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888`.
</details>

<details>
<summary>Run from source (local dev)</summary>

```bash
git clone https://github.com/jamiew/spotify-mcp.git
cd spotify-mcp
uv sync
```

Then point your client at the checkout:

```json
{
  "mcpServers": {
    "spotify": {
      "command": "uv",
      "args": ["--directory", "/path/to/spotify-mcp", "run", "spotify-mcp"],
      "env": {
        "SPOTIFY_CLIENT_ID": "your_client_id",
        "SPOTIFY_CLIENT_SECRET": "your_client_secret",
        "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888"
      }
    }
  }
}
```

To run the latest unpublished commit without cloning: `uvx --from git+https://github.com/jamiew/spotify-mcp.git spotify-mcp`.
</details>

On first use the server opens a browser for Spotify OAuth; the token is cached locally for later runs.
Artist-follow membership now requests `user-follow-read`. Existing grants do not gain scopes
just because code changes: restart the server and complete Spotify reauthorization when
prompted. If Spotify still denies the scope, review the app's grant in your Spotify account
and reauthorize it. **Do not delete the auth cache automatically** or share its contents.

## Usage Examples

- **"Create a chill study playlist with 20 tracks"** → Search + playlist creation + bulk track addition
- **"Show me the first 50 tracks from my 'Liked Songs'"** → Pagination for large playlists  
- **"Find similar artists to Radiohead and add their top tracks to my queue"** → Search + artist info + queue management

## Development

Built with the **FastMCP framework** — focused single-purpose tools spanning playback, search, queue, and playlist management, with type-safe APIs and comprehensive test coverage.
Pydantic is a direct runtime dependency for output schemas. The SDK remains `mcp[cli]<2`;
the CLI extra is retained for MCP CLI development workflows. This compatibility update is
unreleased: no version bump or release publication is required.

**Debug with MCP Inspector:**
```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/spotify_mcp run spotify-mcp
```

## Contributors

- [@jamiew](https://github.com/jamiew)
- [@varunneal](https://github.com/varunneal) — original [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp)
- [@jonico](https://github.com/jonico)
- [@tedeuxx](https://github.com/tedeuxx)
- [@karimStekelenburg](https://github.com/karimStekelenburg)
