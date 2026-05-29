"""Deploy the Spotify MCP server to Modal over streamable-HTTP.

Modal hard-caps each request at ~150s and 303-redirects past that, which breaks
long-lived/stateful streamable-HTTP sessions — so we run FastMCP in **stateless**
mode (`SPOTIFY_MCP_STATELESS=true`). See PLAN.md for the full rationale.

Setup (one-time):
  1. Obtain a Spotify refresh token locally (run the normal stdio server once to
     complete the browser OAuth flow, then read it from the spotipy `.cache` file).
  2. Create the secret:
       modal secret create spotify \\
         SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... SPOTIFY_REFRESH_TOKEN=...
  3. Serve ephemerally:  modal serve deploy/modal_app.py
     Deploy for real:    modal deploy deploy/modal_app.py
  Endpoint: https://<workspace>--spotify-mcp-mcp-web.modal.run/mcp

Connect a client, e.g.:
  claude mcp add --transport http spotify https://<...>.modal.run/mcp
"""

from __future__ import annotations

import modal

app = modal.App("spotify-mcp")

image = modal.Image.debian_slim(python_version="3.12").pip_install_from_pyproject(
    "pyproject.toml"
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("spotify")],
    min_containers=1,  # keep one warm to avoid OAuth/init cold starts
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def mcp_web():  # noqa: ANN201 - Modal infers the ASGI return type
    # Import inside the function so it runs in the Modal container (with secrets set).
    from spotify_mcp.fastmcp_server import build_http_app, mcp

    # Stateless is REQUIRED on Modal (150s request cap breaks stateful sessions).
    mcp.settings.stateless_http = True

    # Optional bearer gate if SPOTIFY_MCP_BEARER is in the secret.
    import os

    return build_http_app(bearer_token=os.getenv("SPOTIFY_MCP_BEARER"))
