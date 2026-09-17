"""Exercise the installed console entrypoint over the real MCP stdio protocol."""

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.integration
async def test_stdio_tool_error_preserves_the_session(tmp_path):
    parameters = StdioServerParameters(
        command=str(Path(sys.executable).parent / "spotify-mcp-jamiew"),
        cwd=tmp_path,
        env={
            "SPOTIFY_CLIENT_ID": "test_client_id",
            "SPOTIFY_CLIENT_SECRET": "test_client_secret",
            "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback",
        },
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=15)
        ) as session:
            await session.initialize()
            # Invalid actions are rejected before any Spotify request or login.
            error = await session.call_tool(
                "control_playback", {"action": "invalid-action"}
            )
            assert error.isError

            # The CLI must keep serving protocol requests after a tool error.
            tools = await session.list_tools()
            playback = next(
                tool for tool in tools.tools if tool.name == "control_playback"
            )
            assert playback.outputSchema is not None
            await session.send_ping()
