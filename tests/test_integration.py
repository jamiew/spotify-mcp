"""Launch the server exactly as the tracked `.mcp.json` does, over real MCP stdio.

`.mcp.json` is the headless dev entry (`claude -p --mcp-config .mcp.json`), so
these tests read it, expand `${CLAUDE_PROJECT_DIR:-.}` to the repo root and run
its command from an unrelated cwd, on both protocol eras.
"""

from __future__ import annotations

import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import ElicitRequestParams, ElicitResult

if TYPE_CHECKING:
    from mcp.client import ClientRequestContext

ROOT = Path(__file__).resolve().parents[1]
ERAS = {"legacy": "2025-11-25", "auto": "2026-07-28"}


def _server(tmp_path: Path) -> StdioServerParameters:
    config = json.loads((ROOT / ".mcp.json").read_text())
    [entry] = config["mcpServers"].values()

    def expand(arg: str) -> str:
        return arg.replace("${CLAUDE_PROJECT_DIR:-.}", str(ROOT))

    # Minimal env: no .env is loaded (cwd is a temp dir) and no real credentials.
    # UV_* passes through so `uv run` reuses CI's interpreter choice.
    env = {k: v for k, v in os.environ.items() if k.startswith("UV_")}
    env |= {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "SPOTIFY_CLIENT_ID": "test_client_id",
        "SPOTIFY_CLIENT_SECRET": "test_client_secret",
        "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback",
    }
    return StdioServerParameters(
        command=expand(entry["command"]),
        args=[expand(a) for a in entry["args"]],
        cwd=tmp_path,
        env=env,
    )


@pytest.mark.integration
@pytest.mark.parametrize("mode", list(ERAS))
async def test_mcp_json_serves_both_eras(tmp_path, mode):
    async with Client(_server(tmp_path), mode=mode, read_timeout_seconds=30) as client:
        assert client.protocol_version == ERAS[mode]
        assert client.server_info is not None
        assert client.server_info.version == version("spotify-mcp-jamiew")

        tools = (await client.list_tools()).tools
        assert tools
        await client.list_prompts()
        await client.list_resources()

        # Clients decide what to auto-run from these hints.
        assert [
            t.name
            for t in tools
            if t.annotations is None or t.annotations.read_only_hint is None
        ] == []
        assert [
            t.name
            for t in tools
            if not t.annotations.read_only_hint
            and t.annotations.destructive_hint is None
        ] == []
        playback = next(t for t in tools if t.name == "control_playback")
        assert playback.output_schema is not None

        # Invalid actions are rejected before any Spotify request or login, and
        # the server keeps serving after a tool error.
        error = await client.call_tool("control_playback", {"action": "invalid-action"})
        assert error.is_error
        assert (await client.list_tools(cache_mode="refresh")).tools


class _Decliner:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def __call__(
        self, context: ClientRequestContext, params: ElicitRequestParams
    ) -> ElicitResult:
        self.questions.append(params.message)
        return ElicitResult(action="decline")


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
        read_timeout_seconds=30,
    ) as client:
        assert client.protocol_version == ERAS[mode]

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
