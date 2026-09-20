![Spotify MCP, Python local server](https://raw.githubusercontent.com/jamiew/spotify-mcp/main/media/readme-header.png)

# spotify-mcp

mcp-name: io.github.jamiew/spotify-mcp

A local Python MCP server for searching Spotify, controlling playback, and managing
playlists and Liked Songs from your MCP client. Runs over stdio with your Spotify account.
This is a supported fork of [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp).

The [TypeScript edition](https://github.com/jamiew/spotify-mcp-cloudflare) supports
local and hosted use, with a different MCP surface.

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Spotify Premium for playback.

1. Create an app in the [Spotify developer dashboard](https://developer.spotify.com/dashboard).
2. Register **`http://127.0.0.1:8888`** as its redirect URI, exactly as written.
3. Copy the app's Client ID and Client Secret into your MCP client's configuration:

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

Restart the client. On first use, a browser opens for Spotify authorization; the token
is cached locally. Keep the client secret and token cache private. Playback needs an
available Spotify Connect device, so open Spotify on the device you want to control.

Client-specific setup: [Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers),
[Claude Code](https://code.claude.com/docs/en/mcp), and
[Codex](https://developers.openai.com/codex/mcp/). Use the same command and environment
variables in each client's format.

### Run from source

The package command above uses the published package, which may lag this README's source
changes. To run the current checkout:

```bash
git clone https://github.com/jamiew/spotify-mcp.git
cd spotify-mcp
uv sync
```

In the config above, change `command` to `uv` and `args` to
`["--directory", "/absolute/path/to/spotify-mcp", "run", "spotify-mcp"]`.
Keep the same environment variables. Without cloning, use
`uvx --from git+https://github.com/jamiew/spotify-mcp.git spotify-mcp`.

**Reauthorization:** artist-follow checks need `user-follow-read`. Existing grants do not
receive new scopes automatically. Restart and complete Spotify authorization when prompted;
if access is still denied, review the app's grant in your Spotify account and reauthorize.
Do not automatically delete the auth cache.

## Features

28 tools, six resources (user, playback, track, playlist, artist, album), and five prompts.
Tools provide typed outputs and behavior annotations; large playlist reads report progress.
Playlist removal asks for confirmation where supported, reordering uses snapshot guards,
and playback control attempts best-effort read-after-write confirmation, not guaranteed success.

### Tools
| Tool | Does |
| --- | --- |
| `get_me` | Read your profile |
| `search_music` | Search tracks, albums, artists, or playlists with filters and pagination |
| `get_tracks` | Read up to 50 tracks; individual reads when batching is unavailable |
| `get_artist` | Read up to 50 artists; top tracks for a single artist |
| `get_album` | Read up to 20 albums; track list for a single album |
| `get_playback_state` | Read current track, device, progress, shuffle, and repeat |
| `control_playback` | Play, pause, skip, seek, volume, shuffle, and repeat |
| `list_devices` | List Spotify Connect devices |
| `transfer_playback` | Move playback to another device |
| `get_queue` | Read now playing and upcoming tracks |
| `add_to_queue` | Queue a track |
| `list_playlists` | List your playlists with pagination |
| `get_playlist` | Read playlist metadata without tracks |
| `get_playlist_tracks` | Read playlist tracks with pagination |
| `create_playlist` | Create a private playlist; set `public=true` to publish |
| `update_playlist_details` | Change name, description, or visibility |
| `add_tracks_to_playlist` | Add up to 100 tracks |
| `remove_tracks_from_playlist` | Remove tracks with confirmation where supported |
| `reorder_playlist` | Move a block of tracks |
| `unfollow_playlist` | Unfollow a playlist, including your own |
| `get_saved_tracks` | Read Liked Songs with pagination |
| `save_tracks` | Like tracks |
| `remove_saved_tracks` | Unlike tracks |
| `check_saved_tracks` | Check up to 50 tracks against Liked Songs |
| `check_saved_albums` | Check up to 20 saved albums |
| `check_following_artists` | Check up to 50 followed artists |
| `get_top_items` | Read top artists or tracks over a time range |
| `get_recently_played` | Read recently played tracks with timestamps |

### Behavior to know

- **New playlists default to private.** Existing playlists are unchanged. Spotify can report
  unexpected visibility, so confirm sensitive playlists in the Spotify app.
- Track saves/removals and track/artist membership checks accept **50 items**, sent upstream
  in batches of **40**. Album checks accept **20**. Membership results preserve input
  alignment; incomplete chunks fail. Writes are not atomic across chunks: earlier changes
  may remain after an error. Check membership before retrying.
- Restricted apps cap search pages at **10**. Advance by the returned `offset + limit`, not
  the requested size. Individual-track fallback can make up to 50 Spotify requests.
- Playlist pagination counts positions, including unresolved rows and local files with
  `id: null`. Local files also set `is_local`; positions remain valid for reordering.

## Spotify access and policy

- Permissions and app access mode determine available endpoints and playlist access.
  A 403 may mean missing permission, not a retired endpoint. Existing integrations' February
  restrictions were [postponed](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security);
  restricted apps use the [February 2026 API routes](https://developer.spotify.com/documentation/web-api/references/changes/february-2026).
- [Development Mode](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
  normally allows five allowlisted users and requires a Premium app owner; older larger
  allowlists may be grandfathered. Extended access is not routine hobby-project approval:
  criteria include an organization, a legal entity, a launched service, and 250,000 monthly
  active users. A public URL does not remove these limits.
- All Development Mode apps on a developer account share one quota, even with the
  [25 permitted Client IDs](https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates).
  This server surfaces 429 errors without retries, including `QUOTA_EXCEEDED` and ordinary
  rate limits with `Retry-After` when provided. No guaranteed quota reset time is specified.
- This server uses **one local identity**. Do not share its token cache or expose it as an
  unauthenticated public service. Other users should self-host and authorize their own accounts.
- **AI policy is a separate constraint.** [Spotify Developer Policy](https://developer.spotify.com/policy)
  III.14 restricts training or otherwise ingesting Spotify Content into AI/ML models;
  "no training" or "metadata only" is not automatic clearance. III.13 restricts analysis and
  derived metrics; III.3 restricts voice-control assistants. Seek Spotify clarification or
  approval before public AI access. This is a risk notice, not legal advice or permission.

### Moving between implementations

Shared versions and tool names do not imply drop-in compatibility with other implementations.
Check arguments, limits, and result shapes before switching. Preserve any workflow that relies
on this server's resources, five prompts, snapshot guards, removal confirmation, or playback
confirmation; those behaviors are not universal.

## Credits

[MIT license](LICENSE), copyright 2025 Varun Neal Srivastava.
Thanks to [@varunneal](https://github.com/varunneal) for the original project,
[@jamiew](https://github.com/jamiew), [@jonico](https://github.com/jonico),
[@tedeuxx](https://github.com/tedeuxx), and
[@karimStekelenburg](https://github.com/karimStekelenburg).

Banner made with [Glif](https://glif.app).
