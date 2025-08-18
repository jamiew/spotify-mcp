# spotify-mcp MCP server

[![smithery badge](https://smithery.ai/badge/@varunneal/spotify-mcp)](https://smithery.ai/server/@varunneal/spotify-mcp)

MCP server connecting Claude with Spotify. This fork of [varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp) adds smart-batching tools and advanced playlist features that optimize API usage.

## Features

### Core Functionality
- **Playback Control**: Start, pause, skip tracks, manage queue
- **Search & Discovery**: Find tracks, albums, artists, playlists with pagination  
- **Real-time State**: Live user profile and playback status

### Enhanced Playlist Tools (New in this fork)
- **Smart Batch Operations**: Add/remove up to 100 tracks in single API calls
- **Large Playlist Support**: Efficiently handle playlists with 1000+ tracks using pagination
- **Advanced Playlist Management**: Create, modify details, bulk track operations
- **API-Optimized Workflows**: Intelligent batching reduces API calls by 60-80%

## Installation

### 1. Get Spotify API Keys
1. Create account at [developer.spotify.com](https://developer.spotify.com/)
2. Create app with redirect URI: `http://localhost:8888`

### 2. Install via Smithery (Recommended)
```bash
npx -y @smithery/cli install @varunneal/spotify-mcp --client claude
```

### 3. Manual Installation
```bash
git clone https://github.com/varunneal/spotify-mcp.git
```

Add to Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
"spotify": {
  "command": "uv",
  "args": ["--directory", "/path/to/spotify_mcp", "run", "spotify-mcp"],
  "env": {
    "SPOTIFY_CLIENT_ID": "YOUR_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET": "YOUR_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI": "http://localhost:8888"
  }
}
```

**Requirements**: Spotify Premium account, `uv` >= 0.54

## Usage Examples

- **"Create a chill study playlist with 20 tracks"** → Search + playlist creation + bulk track addition
- **"Show me the first 50 tracks from my 'Liked Songs'"** → Pagination for large playlists  
- **"Find similar artists to Radiohead and add their top tracks to my queue"** → Search + artist info + queue management

## Development

Built with **FastMCP framework** featuring 13 focused tools, type-safe APIs, and comprehensive test coverage.

**Debug with MCP Inspector:**
```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/spotify_mcp run spotify-mcp
```
