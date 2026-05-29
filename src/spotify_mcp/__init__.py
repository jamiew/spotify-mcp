import os
import signal
import sys

from .fastmcp_server import build_http_app, mcp


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _run_stdio() -> None:
    mcp.run()


def _run_http() -> None:
    """Serve over streamable-HTTP for remote/hosted deploys.

    Config via env: SPOTIFY_MCP_HOST, SPOTIFY_MCP_PORT, SPOTIFY_MCP_STATELESS
    (required on Modal — see PLAN.md), and SPOTIFY_MCP_BEARER (optional token gate).
    """
    host = os.getenv("SPOTIFY_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("SPOTIFY_MCP_PORT", "8000"))
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.stateless_http = _env_bool("SPOTIFY_MCP_STATELESS")

    bearer = os.getenv("SPOTIFY_MCP_BEARER")
    if bearer:
        # Bearer gating needs a custom ASGI wrapper, so run uvicorn ourselves.
        import uvicorn

        uvicorn.run(
            build_http_app(bearer_token=bearer),
            host=host,
            port=port,
            log_level=mcp.settings.log_level.lower(),
        )
    else:
        mcp.run(transport="streamable-http")


def main() -> None:
    """Main entry point for the package."""
    # Handle SIGPIPE gracefully (when client disconnects)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    transport = os.getenv("SPOTIFY_MCP_TRANSPORT", "stdio").strip().lower()

    try:
        if transport == "stdio":
            _run_stdio()
        elif transport in ("streamable-http", "http"):
            _run_http()
        else:
            raise SystemExit(
                f"Unknown SPOTIFY_MCP_TRANSPORT={transport!r} "
                "(expected 'stdio' or 'streamable-http')"
            )
    except BrokenPipeError:
        # Handle broken pipe gracefully when client disconnects
        sys.exit(0)
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        sys.exit(0)


# Optionally expose other important items at package level
__all__ = ["main", "mcp"]
