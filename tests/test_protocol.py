"""Drive the real MCPServer through the SDK's in-process Client on both protocol eras.

`legacy` negotiates 2025-11-25 with an initialize handshake, where the server
pushes elicitation requests. `auto` negotiates 2026-07-28, where there is no
back-channel: a question comes back as an input-required result and the client
retries the call. Spotify is mocked; the MCP layer is not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from mcp import Client
from mcp.types import ElicitRequestParams, ElicitResult
from spotipy import SpotifyException

from spotify_mcp.fastmcp_server import mcp

if TYPE_CHECKING:
    from mcp.client import ClientRequestContext

ERAS = {"legacy": "2025-11-25", "auto": "2026-07-28"}
REMOVE = {"playlist_id": "pl1", "track_uris": ["t1"]}


class Answer:
    """An elicitation callback that records each question and replies with `reply`."""

    def __init__(self, reply: ElicitResult | Exception) -> None:
        self.reply = reply
        self.questions: list[str] = []

    async def __call__(
        self, context: ClientRequestContext, params: ElicitRequestParams
    ) -> ElicitResult:
        self.questions.append(params.message)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture(params=list(ERAS))
def mode(request) -> str:
    return request.param


def _client(mode: str, answer: Answer | None = None) -> Client:
    return Client(mcp, mode=mode, elicitation_callback=answer)


def _text(result) -> str:
    return result.content[0].text


async def test_each_mode_negotiates_its_era(mode):
    async with _client(mode) as client:
        assert client.protocol_version == ERAS[mode]


class TestRemovalConfirmation:
    async def test_confirmed_removal_deletes(self, mode, mock_spotify_api):
        mock_spotify_api._delete.return_value = {"snapshot_id": "s2"}
        answer = Answer(ElicitResult(action="accept", content={"confirm": True}))

        async with _client(mode, answer) as client:
            result = await client.call_tool("remove_tracks_from_playlist", REMOVE)

        assert answer.questions == [
            "Remove 1 track(s) from playlist pl1? This cannot be undone."
        ]
        assert result.structured_content["status"] == "success"
        mock_spotify_api._delete.assert_called_once()

    @pytest.mark.parametrize(
        "reply",
        [
            ElicitResult(action="decline"),
            ElicitResult(action="cancel"),
            ElicitResult(action="accept", content={"confirm": False}),
        ],
        ids=["declined", "cancelled", "accepted-unticked"],
    )
    async def test_unconfirmed_removal_keeps_the_tracks(
        self, mode, mock_spotify_api, reply
    ):
        answer = Answer(reply)

        async with _client(mode, answer) as client:
            result = await client.call_tool("remove_tracks_from_playlist", REMOVE)

        assert len(answer.questions) == 1
        assert not result.is_error
        assert result.structured_content["status"] == "cancelled"
        mock_spotify_api._delete.assert_not_called()

    async def test_clients_without_elicitation_remove_without_asking(
        self, mode, mock_spotify_api
    ):
        mock_spotify_api._delete.return_value = {"snapshot_id": "s2"}

        async with _client(mode) as client:
            result = await client.call_tool("remove_tracks_from_playlist", REMOVE)

        assert result.structured_content["status"] == "success"
        mock_spotify_api._delete.assert_called_once()

    async def test_a_failed_prompt_never_deletes(self, mode, mock_spotify_api):
        answer = Answer(RuntimeError("form crashed"))

        async with _client(mode, answer) as client:
            # Legacy surfaces the failure as a protocol error; 2026-era raises it
            # from the client's own retry loop. Either way the call fails.
            with pytest.raises(Exception):  # noqa: B017
                await client.call_tool("remove_tracks_from_playlist", REMOVE)

        assert answer.questions
        mock_spotify_api._delete.assert_not_called()

    async def test_malformed_uris_fail_before_asking(self, mode, mock_spotify_api):
        answer = Answer(ElicitResult(action="accept", content={"confirm": True}))

        async with _client(mode, answer) as client:
            result = await client.call_tool(
                "remove_tracks_from_playlist",
                {"playlist_id": "pl1", "track_uris": ["spotify:artist:a1"]},
            )

        assert result.is_error
        assert "Only tracks and episodes" in _text(result)
        assert answer.questions == []
        mock_spotify_api._delete.assert_not_called()

    async def test_the_model_cannot_supply_the_confirmation(self, mode):
        async with _client(mode) as client:
            tools = await client.list_tools()

        remove = next(t for t in tools.tools if t.name == "remove_tracks_from_playlist")
        assert set(remove.input_schema["properties"]) == {"playlist_id", "track_uris"}


class TestErrorsReachTheModel:
    """MCPServer v2 replaces the text of unanticipated exceptions with a bare
    "Error executing tool <name>"; the model must still see why a call failed."""

    async def test_spotify_reason_survives(self, mode, mock_spotify_api):
        mock_spotify_api.track.side_effect = SpotifyException(
            403, -1, "forbidden", reason="QUOTA_EXCEEDED"
        )

        async with _client(mode) as client:
            result = await client.call_tool("get_tracks", {"track_ids": "t1"})

        assert result.is_error
        assert "spotify reason: QUOTA_EXCEEDED" in _text(result)

    async def test_argument_errors_survive(self, mode):
        async with _client(mode) as client:
            result = await client.call_tool("control_playback", {"action": "dance"})

        assert result.is_error
        assert "Invalid action: dance" in _text(result)


async def test_large_playlist_reads_report_progress(
    mode, mock_spotify_api, sample_track_data
):
    mock_spotify_api.playlist.return_value = {"tracks": {"total": 2}}
    mock_spotify_api._get.return_value = {
        "items": [{"track": sample_track_data}] * 2,
        "total": 2,
        "next": None,
    }
    updates = []

    async def on_progress(progress, total, message):
        updates.append((progress, total, message))

    async with _client(mode) as client:
        result = await client.call_tool(
            "get_playlist_tracks", {"playlist_id": "pl1"}, progress_callback=on_progress
        )

    assert result.structured_content["returned"] == 2
    assert updates == [(2, 2, "Fetched 2 tracks so far")]
