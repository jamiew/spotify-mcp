# Plan: streamable-HTTP transport + remote deployment

## Context

Today this server is **stdio-only**: `main()` in `src/spotify_mcp/__init__.py` calls
`mcp.run()`, which defaults to stdio, and auth is a module-level singleton
(`spotify_api.Client()`) using spotipy's `SpotifyOAuth` with a local
`CacheFileHandler`. That's great for a local install but means it can't be hosted
anywhere — every client has to run the process itself.

Goal: let the same server *optionally* speak **streamable-HTTP** so it can be deployed
remotely — **homelab first**, with **Modal** as the cloud option (the user has a Modal
account). Competitive research (`research/spotify-mcp-landscape.md`) found that remote/
HTTP transport is rare across Spotify MCPs and a genuine differentiator; the abandoned
leader (varunneal, 602★) is stdio-only, and only niche projects (iceener, qchuchu) are
remote-capable.

The transport flag is the *easy* part. The real work is **headless OAuth**: a hosted
server has no browser to complete the Spotify auth dance and an ephemeral filesystem, so
the refresh token must be supplied out-of-band and persisted.

## What's already in our favor

- FastMCP 1.27.1 (`from mcp.server.fastmcp import FastMCP`) **already supports**
  `transport="streamable-http"` and exposes `mcp.streamable_http_app()` (a Starlette ASGI
  app) plus settings: `host`, `port`, `streamable_http_path`, `stateless_http`,
  `json_response`, `auth`. No dependency bump needed.
- A `Dockerfile` already exists (multi-stage, non-root) — it just runs the stdio command.
- spotipy ships `MemoryCacheHandler` and `RedisCacheHandler` alongside `CacheFileHandler`,
  so headless token seeding is supported without new deps.

> Note: we use the **official `mcp` SDK's** FastMCP, not the standalone `fastmcp`
> (gofastmcp) package. So it's `mcp.settings.stateless_http = True` + `mcp.run(...)` /
> `mcp.streamable_http_app()` — *not* `mcp.http_app(...)`, which is the other package.

## Implementation

### 1. Make transport configurable — `src/spotify_mcp/__init__.py`

Select transport from env so stdio stays the zero-config default:

- `SPOTIFY_MCP_TRANSPORT` = `stdio` (default) | `streamable-http`
- when http: read `SPOTIFY_MCP_HOST` (default `127.0.0.1`), `SPOTIFY_MCP_PORT`
  (default `8000`), and `SPOTIFY_MCP_STATELESS` (default off; **set on Modal**).
- set `mcp.settings.host/port/stateless_http` before `mcp.run(transport=...)`.

Keep the SIGPIPE/BrokenPipe handling for stdio; it's harmless under http.

### 2. Headless OAuth — `src/spotify_mcp/spotify_api.py` (the actual work)

Current `Client.__init__` always builds a default file-cache `SpotifyOAuth`. Add a
headless path that activates when a refresh token / explicit cache location is provided:

- **`SPOTIFY_REFRESH_TOKEN`** present → seed a `MemoryCacheHandler` with a token_info
  dict (`{"refresh_token": ..., "scope": <SCOPES>, "expires_at": 0}`); spotipy refreshes
  the access token automatically on first call. No browser, no disk needed.
- else **`SPOTIFY_CACHE_PATH`** present → `CacheFileHandler(cache_path=...)` pointed at a
  persistent volume (Modal Volume / Docker bind mount / Render disk).
- pass `open_browser=False` to `SpotifyOAuth` so a hosted process never tries to launch a
  browser.
- one-time bootstrap: document running the existing local OAuth flow once to mint the
  refresh token, then copy it into the deploy target's secret store.

This keeps the **single-user** model (one Spotify account per deployment) — correct for a
homelab. Multi-user/public hosting (per-session tokens via the MCP `auth` layer) is
explicitly **out of scope** for v1; note it as a future step.

### 3. Transport-layer auth for remote (lightweight)

Single-user homelab doesn't need full OAuth 2.1. Plan:

- **Network boundary** (recommended): expose only over Tailscale or a Cloudflare Tunnel;
  no public port. Clients add the server with `claude mcp add --transport http`.
- **Optional bearer token**: a tiny ASGI middleware on `streamable_http_app()` checking
  `Authorization: Bearer <SPOTIFY_MCP_BEARER>`; clients pass
  `--header "Authorization: Bearer …"`. Skip if behind Tailscale.

### 4. Docker — `Dockerfile`

Add an http-friendly variant: `ENV SPOTIFY_MCP_TRANSPORT=streamable-http
SPOTIFY_MCP_HOST=0.0.0.0`, `EXPOSE 8000`. Keep stdio as the default CMD path so existing
users are unaffected; http is opt-in via env.

### 5. Modal deploy script — `deploy/modal_app.py` (new)

```python
import modal
app = modal.App("spotify-mcp")
image = modal.Image.debian_slim().pip_install_from_pyproject("pyproject.toml")
vol = modal.Volume.from_name("spotify-cache", create_if_missing=True)

@app.function(image=image, secrets=[modal.Secret.from_name("spotify")],
              volumes={"/cache": vol}, min_containers=1)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def mcp_web():
    from spotify_mcp.fastmcp_server import mcp
    mcp.settings.stateless_http = True   # REQUIRED on Modal (see below)
    return mcp.streamable_http_app()
```

- `modal.Secret` "spotify" holds `SPOTIFY_CLIENT_ID/SECRET` + `SPOTIFY_REFRESH_TOKEN`.
- **Stateless is mandatory on Modal**: Modal hard-caps a request at **150s** and
  303-redirects past that, which breaks long-lived SSE / stateful streamable-HTTP
  sessions. `stateless_http=True` makes each request self-contained so autoscaling is
  safe. `min_containers=1` avoids cold starts; ~\$5/mo compute, inside the \$30 free credit.
- Caveat to verify: our tools that stream **progress notifications** (`get_playlist_tracks`
  via `ctx.report_progress`) — confirm they still function under `stateless_http=True`
  within the 150s window on very large playlists.

## Deployment options (recommendation: homelab now, Modal next)

| Target | Stateful sessions? | Token persistence | Notes |
|---|---|---|---|
| **Homelab** (recommended first) | ✅ yes (no caps) | Docker bind mount → `SPOTIFY_CACHE_PATH` | Caddy/Traefik for TLS, or Tailscale/CF Tunnel (no open ports). Cleanest fit. |
| **Modal** | ❌ stateless only | `modal.Volume` or `SPOTIFY_REFRESH_TOKEN` secret | 150s request cap → must run stateless; `min_containers=1`. |
| Fly.io | ✅ (1 always-on machine) | Fly Volume | managed; volume pins region; no free tier. |
| Render | ✅ (single instance + disk) | Render Disk | avoid free tier (15-min sleep, ~1m cold start). |

## Verification

1. **Local http smoke test**: `SPOTIFY_MCP_TRANSPORT=streamable-http uv run spotify-mcp`,
   then connect with the MCP Inspector or `claude mcp add --transport http spotify
   http://127.0.0.1:8000/mcp`; list tools, run `search_tracks`, `get_currently_playing`.
2. **Headless auth**: with only `SPOTIFY_REFRESH_TOKEN` set (no `.cache` file), confirm a
   tool call succeeds (token auto-refreshes) and no browser is attempted.
3. **Tests/gates**: `uv run mypy src/` + `uv run pytest`. Add tests for the transport
   env-var selection and the headless `Client` path (mock spotipy; assert
   `MemoryCacheHandler` is used and `open_browser=False`).
4. **Modal**: `modal serve deploy/modal_app.py` (ephemeral) → hit `/mcp`; then
   `modal deploy`. Verify the Volume persists the refreshed token across a container
   restart, and a large-playlist progress stream completes under 150s in stateless mode.
5. **Regression**: default `uv run spotify-mcp` (no env) still starts stdio unchanged.

## Critical files

- `src/spotify_mcp/__init__.py` — transport selection
- `src/spotify_mcp/spotify_api.py` — headless OAuth (`MemoryCacheHandler` / `cache_path`,
  `open_browser=False`)
- `src/spotify_mcp/fastmcp_server.py` — optional bearer-auth middleware on the ASGI app
- `Dockerfile` — http env/EXPOSE
- `deploy/modal_app.py` — new, Modal ASGI app
- `README.md` / `CHANGELOG.md` — document remote mode

## Out of scope (future)

- Multi-user / public hosting with per-session Spotify OAuth via the MCP auth spec
  (OAuth 2.1 + PKCE, `/.well-known/oauth-protected-resource`).
- Publishing a hosted endpoint to the MCP registry as a `remote` transport entry.
