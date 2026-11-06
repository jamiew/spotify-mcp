"""Exercise the installed console entrypoint over the real MCP stdio protocol."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitRequestParams, ElicitResult

if TYPE_CHECKING:
    from mcp.client import ClientRequestContext

MODERN_ERA = "2026-07-28"


def _server(tmp_path: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=str(Path(sys.executable).parent / "spotify-mcp-jamiew"),
        cwd=tmp_path,
        env={
            "SPOTIFY_CLIENT_ID": "test_client_id",
            "SPOTIFY_CLIENT_SECRET": "test_client_secret",
            "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback",
        },
    )


class _Decliner:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def __call__(
        self, context: ClientRequestContext, params: ElicitRequestParams
    ) -> ElicitResult:
        self.questions.append(params.message)
        return ElicitResult(action="decline")


@pytest.mark.integration
async def test_stdio_tool_error_preserves_the_session(tmp_path):
    async with stdio_client(_server(tmp_path)) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=15) as session:
            await session.initialize()
            # Invalid actions are rejected before any Spotify request or login.
            error = await session.call_tool(
                "control_playback", {"action": "invalid-action"}
            )
            assert error.is_error

            # The CLI must keep serving protocol requests after a tool error.
            tools = await session.list_tools()
            playback = next(
                tool for tool in tools.tools if tool.name == "control_playback"
            )
            assert playback.output_schema is not None
            await session.send_ping()


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_stdio_removal_asks_before_touching_spotify(tmp_path, mode):
    """Legacy clients get a pushed elicitation; 2026-era clients get the question
    back as an input-required result. Declining must stop before Spotify, which
    with fake credentials would otherwise fail on login."""
    decline = _Decliner()
    async with Client(
        _server(tmp_path),
        mode=mode,
        elicitation_callback=decline,
        read_timeout_seconds=15,
    ) as client:
        assert (client.protocol_version == MODERN_ERA) == (mode == "auto")

        result = await client.call_tool(
            "remove_tracks_from_playlist",
            {"playlist_id": "pl1", "track_uris": ["t1"]},
        )

    assert decline.questions == [
        "Remove 1 track(s) from playlist pl1? This cannot be undone."
    ]
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["status"] == "cancelled"
