# spotify-mcp MCP server

mcp-name: io.github.jamiew/spotify-mcp

MCP server connecting Claude with Spotify. This fork of [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp) adds smart-batching tools and advanced playlist features that optimize API usage.

## Features

### Core Functionality
- **Playback Control**: Start, pause, skip tracks, manage queue
- **Search & Discovery**: Find tracks, albums, artists, playlists with pagination  
- **Real-time State**: Live user profile and playback status
- **Resources**: Read user, playback, track, playlist, artist, and album state by URI

### Modern MCP Protocol
- **Structured output**: every tool returns a typed schema, not a bare dict
- **Tool annotations & icons**: read-only/destructive hints, titles, and a Spotify glyph
- **Progress notifications**: live updates while paginating large playlists
- **Elicitation**: destructive playlist removals ask for confirmation on clients that support it

### Enhanced Playlist Tools (New in this fork)
- **Smart Batch Operations**: Add/remove up to 100 tracks in single API calls
- **Large Playlist Support**: Efficiently handle playlists with 1000+ tracks using pagination
- **Advanced Playlist Management**: Create, modify details, reorder tracks, bulk track operations
- **API-Optimized Workflows**: Intelligent batching reduces API calls by 60-80%

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

## Remote hosting (streamable-HTTP)

The server speaks stdio by default, but can serve **streamable-HTTP** for remote/hosted
use. Set `SPOTIFY_MCP_TRANSPORT=streamable-http` (optionally `SPOTIFY_MCP_HOST`,
`SPOTIFY_MCP_PORT`); clients connect with `claude mcp add --transport http spotify <url>/mcp`.

Because a hosted process has no browser and often an ephemeral disk, supply the OAuth token
out-of-band: complete the browser flow once locally, then set `SPOTIFY_REFRESH_TOKEN` (or
point `SPOTIFY_CACHE_PATH` at a persistent volume). Optional `SPOTIFY_MCP_BEARER` gates the
endpoint behind an `Authorization: Bearer …` header — otherwise keep it on a private network
(Tailscale / Cloudflare Tunnel).

For a one-command Modal deploy and the homelab/Fly/Render trade-offs, see `PLAN.md` and
`deploy/modal_app.py`. Note Modal requires stateless mode (`SPOTIFY_MCP_STATELESS=true`).

## Usage Examples

- **"Create a chill study playlist with 20 tracks"** → Search + playlist creation + bulk track addition
- **"Show me the first 50 tracks from my 'Liked Songs'"** → Pagination for large playlists  
- **"Find similar artists to Radiohead and add their top tracks to my queue"** → Search + artist info + queue management

## Development

Built with the **FastMCP framework** — focused single-purpose tools spanning playback, search, queue, and playlist management, with type-safe APIs and comprehensive test coverage.

**Debug with MCP Inspector:**
```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/spotify_mcp run spotify-mcp
```
