# Spotify MCP landscape — competitive research (May 2026)

Survey of other Spotify MCP servers across GitHub, the official MCP Registry, Smithery,
Glama, PulseMCP, and hosted-MCP providers. Goal: understand what exists, whether anything
is remotely hosted with managed OAuth, and whether we should switch off our local server.

## TL;DR

- **Don't switch.** Our server is one of the most feature-complete in the field and the only
  actively-maintained Python one with typed structured output. Nothing else dominates it.
- **The market leader (varunneal, 602★) is officially abandoned** as of March 2026. We descend
  from it — clean narrative as the maintained, token-efficient successor.
- **A capable remote-hosted Spotify MCP with managed OAuth does now exist:** `sptfy-mcp.online`
  (akutishevsky) — connect by URL, ~80 tools, OAuth walked through on first connect, free.
  Composio is the managed-platform alternative. Both are worth a look *if* remote is the goal,
  but neither has our structured-output/elicitation/registry-published polish.
- **Universal caveat:** playback control requires **Spotify Premium** on *every* server — it's a
  Spotify Web API restriction, not a server choice.

## Our server (jamiew/spotify-mcp) — baseline

- Python / FastMCP, ~14 tools + 6 resources + 3 prompts. PyPI `spotify-mcp-jamiew`,
  registry `io.github.jamiew/spotify-mcp`. 7★.
- Tools: search (track/artist/album/playlist), playback info + control, queue, library
  (saved/top/recent, save/remove), playlist create/add/remove. Read-only vs destructive
  annotations; elicitation confirmation on destructive playlist ops.
- **Differentiators:** typed Pydantic structured-output schemas (real output schema, not raw
  JSON), tool annotations + icons, progress notifications on long paginations, elicitation
  prompts, MCP-registry published. Token-efficient via selective field extraction.
- **Gaps:** stdio/local only (not remote-hostable without a token-persistence layer);
  no playlist reorder; recommendations/audio-features gone (Spotify deprecated Nov 2024).

## GitHub field

| Project | ★ | Lang | Design | Remote? | Playlist | Maintained | Notes |
|---|---|---|---|---|---|---|---|
| **varunneal/spotify-mcp** | 602 | Py (spotipy) | multiplexed `action` tools | stdio only | create/update | **Abandoned** (Mar 2026 "inactive") | OG, most mindshare. We fork its lineage. |
| **marcelmarais/spotify-mcp-server** | 356 | TS | ~20 one-tool-per-action | stdio only | create+add (no reorder) | **Active** (May 2026) | Healthiest rival; great docs; volume/device granularity. |
| **iceener/spotify-streamable-mcp-server** | 80 | TS (Node/Bun **or** CF Workers) | **5 batch tools** | **Yes — streamable HTTP** | **Full CRUD incl. reorder** | active-ish (Feb 2026) | Our philosophical twin: batch-first, slim outputs, OAuth2.1+PKCE, device transfer. No Pydantic schemas/elicitation. |
| **superseoworld/mcp-spotify** ("ArtistLens") | 20 | TS | catalog/metadata | stdio | read + metadata edit | stale (Mar 2025) | **client-credentials only** → no playback, no Premium. Audiobooks. Easy Smithery install. |
| qchuchu/spotify-mcp-server | 9 | TS | search/playlist/playback | **Yes (hosted demo)** | yes | Sep 2025 | Live Alpic-hosted instance; demo-grade. |
| LibreChat-AI/spotify-mcp | 10 | TS (CF Workers) | OAuth reference | self-deploy | — | Mar 2026 | OAuth 2.0+PKCE + DCR discovery example, not a full player. |
| Carrieukie/spotify-mcp-server | 19 | **Kotlin** | playback/playlist | stdio **+ SSE** | yes | Jul 2025 | Rare Kotlin; supports SSE. |
| vsaez/mcp-spotify-player | 19 | Py | playback | stdio | — | Sep 2025 | Polished CI. |

Plus many sub-5★ forks/clones (igorgarbuz, sespinosa, belljustin, tylerpina, thebigredgeek…).

## Remote-hosted / managed-OAuth options (the part you cared about)

True connect-by-URL servers:

1. **akutishevsky — `https://sptfy-mcp.online/mcp`** ★ best match. Managed PKCE OAuth (auth
   walked through on first connect, **no API keys / no Spotify dev app**), ~80 tools across ~13
   categories, full Web API coverage, TOON-formatted (token-efficient) responses, free.
   Registry: `io.github.akutishevsky/spotify`. *Unverified:* exact rate limits and whether it
   clears Spotify's 25-user dev-mode cap (repo README wasn't publicly fetchable). One third
   party holds your token (encrypted per their claim) — trust check warranted.
2. **Composio — `https://mcp.composio.dev/spotify`** ~84 tools, managed token lifecycle
   (refresh/rotation), but likely BYO Spotify client ID/secret at setup. Enterprise/managed-
   platform flavor. Free tier + paid.
3. **ai.trendsmcp/spotify** — Bearer/API-key, **analytics/trends data only**, no playback/
   playlists. Not a control server.
4. **pipeworx-io/spotify** — `client_credentials`, **catalog read only**, no user account.
5. **Zapier MCP — Spotify** — hosted URL, but **automation actions, not real-time playback**.
6. **Klavis (Strata)** — hosted MCP w/ managed OAuth, but **couldn't confirm a Spotify connector**.

Self-deploy "remote-capable" (BYO creds, no public endpoint): TylerLeonhardt/spotify-remote-mcp,
LibreChat-AI/spotify-mcp, and Smithery-buildable servers (@superseoworld, @latiftplgu, @obre10off…).

## Registry counts

- Official MCP Registry: 6 spotify entries — 3 remote HTTP (akutishevsky, trendsmcp, pipeworx),
  3 local stdio (jamiew = us, khglynn, markswendsen/striderlabs).
- PulseMCP: "Top 30 Spotify MCP Servers", nearly all local/self-host.
- Glama: 20 listed (7 remote-capable, 7 local-only, 1 hybrid, 5 unspecified). Glama doesn't host.
- Smithery: several (exact count blocked by rate-limiting during research).

## Takeaways for positioning

1. Lead with "**maintained, token-efficient, structured-output successor to varunneal**" — the
   602★ leader is dead and there's no active high-star Python competitor.
2. Our real differentiators even vs. iceener: **typed Pydantic output schemas, elicitation
   confirmations, progress notifications, registry-published**. Few/none combine these.
3. **Gaps worth closing:** playlist **reorder** (only iceener clearly has it), and **remote/HTTP
   transport** (rare across the field — genuine opening if we want to be hostable).
4. State the **Premium requirement** plainly in the README; users repeatedly trip on it.

Sources: github.com/varunneal/spotify-mcp, github.com/marcelmarais/spotify-mcp-server,
github.com/iceener/spotify-streamable-mcp-server, github.com/superseoworld/mcp-spotify,
sptfy-mcp.online, composio.dev/toolkits/spotify, zapier.com/mcp/spotify,
registry.modelcontextprotocol.io (search=spotify), pulsemcp.com/servers?q=spotify,
glama.ai/mcp/servers?query=spotify.
