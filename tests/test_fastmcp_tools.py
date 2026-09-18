"""Tests for FastMCP server tools, resources, and prompts.

External Spotify calls are mocked at the spotipy-client boundary only; every
test asserts on the real transformation/validation logic in the tools.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from spotipy import SpotifyException

from spotify_mcp.fastmcp_server import (
    PlaybackState,
    Playlist,
    Track,
    add_to_queue,
    add_tracks_to_playlist,
    album_resource,
    analyze_large_playlist,
    artist_resource,
    check_following_artists,
    check_saved_albums,
    check_saved_tracks,
    control_playback,
    create_mood_playlist,
    create_playlist,
    current_playback_resource,
    current_user,
    discover_music_systematically,
    get_album,
    get_artist,
    get_me,
    get_playback_state,
    get_playlist,
    get_playlist_tracks,
    get_queue,
    get_recently_played,
    get_saved_tracks,
    get_top_items,
    get_tracks,
    list_devices,
    list_playlists,
    playlist_resource,
    remove_saved_tracks,
    remove_tracks_from_playlist,
    reorder_playlist,
    save_tracks,
    search_music,
    track_resource,
    unfollow_playlist,
    update_playlist_details,
)

# A SpotifyException the tools should translate into a ValueError.
SPOTIFY_ERROR = SpotifyException(404, -1, "track not found")


class TestGetPlaybackState:
    def test_get_playback_state(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_playback.return_value = sample_playback_data

        result = get_playback_state()

        assert isinstance(result, PlaybackState)
        assert result.is_playing
        assert result.track is not None
        assert result.track.name == "Never Gonna Give You Up"
        assert result.device == "My iPhone"
        assert result.volume == 70
        mock_spotify_api.current_playback.assert_called_once()

    def test_no_active_playback(self, mock_spotify_api):
        mock_spotify_api.current_playback.return_value = None

        result = get_playback_state()

        assert isinstance(result, PlaybackState)
        assert result.is_playing is False
        assert result.track is None

    def test_local_file_playback(self, mock_spotify_api, sample_playback_data):
        # parse_track is shared with playlist reads: a local file must not raise here
        local = {**sample_playback_data["item"], "id": None, "is_local": True}
        mock_spotify_api.current_playback.return_value = {
            **sample_playback_data,
            "item": local,
        }

        result = get_playback_state()

        assert result.track is not None
        assert result.track.id is None
        assert result.track.is_local is True

    def test_spotify_error_becomes_value_error(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_playback_state()


class TestControlPlayback:
    async def test_play_tracks(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_playback.return_value = sample_playback_data

        await control_playback("play", track_ids=["4iV5W9uYEdYUVa79Axb7Rh"])

        mock_spotify_api.start_playback.assert_called_once_with(
            device_id=None, uris=["spotify:track:4iV5W9uYEdYUVa79Axb7Rh"]
        )

    async def test_play_context_wins_over_tracks(
        self, mock_spotify_api, sample_playback_data
    ):
        mock_spotify_api.current_playback.return_value = sample_playback_data

        await control_playback("play", context_uri="spotify:album:x", track_ids=["abc"])

        mock_spotify_api.start_playback.assert_called_once_with(
            device_id=None, context_uri="spotify:album:x"
        )

    async def test_seek_without_position_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="position_ms"):
            await control_playback("seek")

    async def test_bad_repeat_state_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="repeat"):
            await control_playback("repeat", state="on")

    async def test_invalid_action_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="Invalid action"):
            await control_playback("teleport")


class TestControlPlaybackReadBack:
    """Spotify applies player changes asynchronously, so the state is read back until
    it reflects the action. These drive the stale-then-fresh sequence a single
    immediate read would have returned wrongly."""

    TRACK = {
        "name": "Never Gonna Give You Up",
        "artists": [{"name": "Rick Astley", "id": "0gxyHStUsqpMadRV0Di1Qt"}],
        "album": {"name": "Whenever You Need Somebody", "id": "6XzKGcM6laRkTrME3rQvJw"},
        "duration_ms": 213573,
    }

    def _playing(self, track_id="4iV5W9uYEdYUVa79Axb7Rh", **over):
        state = {
            "is_playing": True,
            "item": {**self.TRACK, "id": track_id},
            "device": {"name": "My iPhone", "volume_percent": 70},
            "shuffle_state": False,
            "repeat_state": "off",
            "progress_ms": 1000,
        }
        state.update(over)
        return state

    async def test_play_waits_out_an_idle_device(self, mock_spotify_api):
        """A waking device answers None, which reads back as an empty, not-playing
        state. The old code returned that."""
        mock_spotify_api.current_playback.side_effect = [
            None,
            None,
            self._playing(),
        ]

        result = await control_playback("play")

        assert result.is_playing is True
        assert result.track is not None
        assert result.track.id == "4iV5W9uYEdYUVa79Axb7Rh"

    async def test_play_waits_past_the_pre_action_state(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(is_playing=False),
            self._playing(),
        ]

        assert (await control_playback("play")).is_playing is True

    async def test_pause_waits_for_playback_to_stop(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(),
            self._playing(is_playing=False),
        ]

        assert (await control_playback("pause")).is_playing is False

    async def test_next_waits_for_the_track_to_change(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(track_id="old"),  # pre-action read
            self._playing(track_id="old"),  # still stale
            self._playing(track_id="new"),
        ]

        result = await control_playback("next")

        assert result.track is not None
        assert result.track.id == "new"

    async def test_next_can_leave_a_local_track(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(
                item={
                    **self.TRACK,
                    "id": None,
                    "is_local": True,
                    "uri": "spotify:local:artist:album:track:213",
                }
            ),
            self._playing(track_id="catalog"),
        ]

        result = await control_playback("next")

        mock_spotify_api.next_track.assert_called_once()
        assert result.track is not None
        assert result.track.id == "catalog"

    async def test_polling_rate_limit_preserves_last_observation(
        self, mock_spotify_api, caplog
    ):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(is_playing=False, progress_ms=1234),
            SpotifyException(429, -1, "rate limited", headers={"Retry-After": "60"}),
            self._playing(),
        ]

        result = await control_playback("play")

        assert result.is_playing is False
        assert result.progress_ms == 1234
        mock_spotify_api.start_playback.assert_called_once()
        assert mock_spotify_api.current_playback.call_count == 2
        assert any(
            record.levelname == "WARNING" and "429" in record.getMessage()
            for record in caplog.records
        )

    async def test_write_failure_still_raises(self, mock_spotify_api):
        mock_spotify_api.start_playback.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError) as exc:
            await control_playback("play")

        assert exc.value.__cause__ is SPOTIFY_ERROR
        mock_spotify_api.start_playback.assert_called_once()
        mock_spotify_api.current_playback.assert_not_called()

    async def test_polling_interval_allows_event_loop_progress(
        self, mock_spotify_api, monkeypatch
    ):
        monkeypatch.setattr("spotify_mcp.fastmcp_server._CONFIRM_DELAY_S", 0.01)
        advanced = asyncio.Event()
        asyncio.get_running_loop().call_soon(advanced.set)
        mock_spotify_api.current_playback.side_effect = lambda: self._playing(
            is_playing=advanced.is_set()
        )

        result = await control_playback("play")

        assert advanced.is_set()
        assert result.is_playing is True

    async def test_shuffle_waits_for_the_flag(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(shuffle_state=False),
            self._playing(shuffle_state=True),
        ]

        assert (await control_playback("shuffle", state="on")).shuffle is True

    async def test_volume_waits_for_the_level(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(device={"name": "My iPhone", "volume_percent": 70}),
            self._playing(device={"name": "My iPhone", "volume_percent": 30}),
        ]

        assert (await control_playback("volume", volume_percent=30)).volume == 30

    async def test_seek_rejects_a_stale_position(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = [
            self._playing(progress_ms=1000),
            self._playing(progress_ms=42120),
        ]

        assert (await control_playback("seek", position_ms=42000)).progress_ms == 42120

    async def test_gives_up_rather_than_hanging(self, mock_spotify_api):
        """A device that never reports the change must not block or raise — the last
        state read is returned, matching the old behaviour."""
        mock_spotify_api.current_playback.side_effect = [
            self._playing(is_playing=False, progress_ms=position)
            for position in range(5)
        ]

        result = await control_playback("play")

        assert result.is_playing is False
        assert result.progress_ms == 4


class TestListDevices:
    def test_lists_devices(self, mock_spotify_api):
        mock_spotify_api.devices.return_value = {
            "devices": [
                {
                    "id": "dev1",
                    "name": "Kitchen",
                    "type": "Speaker",
                    "is_active": True,
                    "volume_percent": 50,
                }
            ]
        }

        result = list_devices()

        assert [d.name for d in result.devices] == ["Kitchen"]
        assert result.devices[0].is_active is True


class TestSearchTracks:
    def test_basic_track_search(self, mock_spotify_api, sample_search_results):
        mock_spotify_api.search.return_value = sample_search_results

        result = search_music("Never Gonna Give You Up")

        assert len(result.items) == 1
        assert isinstance(result.items[0], Track)
        assert result.items[0].name == "Never Gonna Give You Up"
        assert result.total == 1
        mock_spotify_api.search.assert_called_once_with(
            q="Never Gonna Give You Up", type="track", limit=10, offset=0
        )

    def test_search_artist_type(self, mock_spotify_api):
        mock_spotify_api.search.return_value = {
            "artists": {
                "items": [{"id": "a1", "name": "Rick Astley", "external_urls": {}}],
                "total": 1,
                "limit": 10,
                "offset": 0,
            }
        }

        result = search_music("Rick Astley", qtype="artist")

        assert result.items[0].name == "Rick Astley"

    def test_search_builds_filtered_query(
        self, mock_spotify_api, sample_search_results
    ):
        mock_spotify_api.search.return_value = sample_search_results

        search_music("love", year="2024", genre="pop", artist="Foo")

        mock_spotify_api.search.assert_called_once_with(
            q="love artist:Foo year:2024 genre:pop", type="track", limit=10, offset=0
        )

    def test_search_builds_album_and_year_range_filters(
        self, mock_spotify_api, sample_search_results
    ):
        mock_spotify_api.search.return_value = sample_search_results

        search_music("love", album="Greatest Hits", year_range="2020-2024")

        mock_spotify_api.search.assert_called_once_with(
            q="love album:Greatest Hits year:2020-2024",
            type="track",
            limit=10,
            offset=0,
        )

    def test_search_album_type_converts_items(self, mock_spotify_api):
        mock_spotify_api.search.return_value = {
            "albums": {
                "items": [
                    {
                        "id": "al1",
                        "name": "Album X",
                        "artists": [{"name": "Band"}],
                        "external_urls": {},
                    }
                ],
                "total": 1,
                "limit": 10,
                "offset": 0,
            }
        }

        result = search_music("x", qtype="album")

        assert result.items[0].name == "Album X"
        assert result.items[0].artist == "Band"

    def test_limit_is_clamped(self, mock_spotify_api, sample_search_results):
        mock_spotify_api.search.return_value = sample_search_results

        search_music("test", limit=999)

        mock_spotify_api.search.assert_called_once_with(
            q="test", type="track", limit=50, offset=0
        )

    def test_empty_results(self, mock_spotify_api):
        mock_spotify_api.search.return_value = {
            "tracks": {"items": [], "total": 0, "limit": 10, "offset": 0}
        }

        result = search_music("nonexistent")

        assert result.items == []
        assert result.total == 0

    def test_spotify_error_becomes_value_error(self, mock_spotify_api):
        mock_spotify_api.search.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            search_music("test")


class TestQueue:
    def test_add_to_queue_converts_id_to_uri(self, mock_spotify_api):
        result = add_to_queue("4iV5W9uYEdYUVa79Axb7Rh")

        assert result.status == "success"
        mock_spotify_api.add_to_queue.assert_called_once_with(
            "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
        )

    @pytest.mark.parametrize(
        "given",
        [
            "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
            "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh",
        ],
    )
    def test_add_to_queue_accepts_uris_and_urls(self, mock_spotify_api, given):
        # A URI used to be pasted into "spotify:track:" a second time and rejected
        add_to_queue(given)

        mock_spotify_api.add_to_queue.assert_called_once_with(
            "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
        )

    def test_add_to_queue_error(self, mock_spotify_api):
        mock_spotify_api.add_to_queue.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            add_to_queue("badid")

    def test_get_queue(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.queue.return_value = {
            "currently_playing": sample_track_data,
            "queue": [sample_track_data, sample_track_data],
        }

        result = get_queue()

        assert result.currently_playing is not None
        assert result.currently_playing.name == "Never Gonna Give You Up"
        assert len(result.queue) == 2

    def test_get_queue_empty(self, mock_spotify_api):
        mock_spotify_api.queue.return_value = {"currently_playing": None, "queue": []}

        result = get_queue()

        assert result.currently_playing is None
        assert result.queue == []

    def test_get_queue_error(self, mock_spotify_api):
        mock_spotify_api.queue.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_queue()


class TestGetTracks:
    def test_single_track(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.track.return_value = sample_track_data

        result = get_tracks("4iV5W9uYEdYUVa79Axb7Rh")

        assert len(result.tracks) == 1
        assert result.tracks[0].artist == "Rick Astley"
        mock_spotify_api.track.assert_called_once_with("4iV5W9uYEdYUVa79Axb7Rh")

    def test_batch_tracks(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.tracks.return_value = {
            "tracks": [sample_track_data, sample_track_data]
        }

        result = get_tracks(["id1", "id2"])

        assert len(result.tracks) == 2
        mock_spotify_api.tracks.assert_called_once_with(["id1", "id2"])

    def test_too_many_ids_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="Maximum 50"):
            get_tracks([f"id{i}" for i in range(51)])

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.track.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_tracks("badid")


class TestGetArtist:
    def test_success(self, mock_spotify_api, sample_artist_data, sample_track_data):
        mock_spotify_api.artist.return_value = sample_artist_data
        mock_spotify_api.artist_top_tracks.return_value = {
            "tracks": [sample_track_data]
        }

        result = get_artist("0gxyHStUsqpMadRV0Di1Qt")

        assert [a.name for a in result.artists] == ["Rick Astley"]
        assert result.artists[0].followers == 1234567
        assert result.artists[0].genres == ["dance pop", "new wave pop"]
        assert len(result.top_tracks) == 1
        assert result.top_tracks[0].name == "Never Gonna Give You Up"

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.artist.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_artist("badid")

    def test_withheld_top_tracks_still_returns_artist(
        self, mock_spotify_api, sample_artist_data
    ):
        """Restricted apps get 403 from /top-tracks; the artist must still come back."""
        mock_spotify_api.artist.return_value = sample_artist_data
        mock_spotify_api.artist_top_tracks.side_effect = SpotifyException(
            403, -1, "Forbidden"
        )

        result = get_artist("0gxyHStUsqpMadRV0Di1Qt")

        assert [a.name for a in result.artists] == ["Rick Astley"]
        assert result.top_tracks == []

    @pytest.mark.parametrize("status", [401, 429, 500])
    def test_other_top_tracks_errors_still_raise(
        self, mock_spotify_api, sample_artist_data, status
    ):
        """Authentication, quota, and server errors are not withheld endpoints."""
        mock_spotify_api.artist.return_value = sample_artist_data
        error = SpotifyException(status, -1, "Request failed", reason="TEST_REASON")
        mock_spotify_api.artist_top_tracks.side_effect = error

        with pytest.raises(ValueError) as raised:
            get_artist("0gxyHStUsqpMadRV0Di1Qt")
        assert raised.value.__cause__ is error

    def test_batches_several_artists_into_one_request(
        self, mock_spotify_api, sample_artist_data
    ):
        second = {**sample_artist_data, "id": "a2", "name": "Second"}
        mock_spotify_api.artists.return_value = {
            "artists": [sample_artist_data, second]
        }

        result = get_artist(["0gxyHStUsqpMadRV0Di1Qt", "a2"])

        assert [a.name for a in result.artists] == ["Rick Astley", "Second"]
        mock_spotify_api.artists.assert_called_once_with(
            ["0gxyHStUsqpMadRV0Di1Qt", "a2"]
        )
        # top tracks are per artist, so a batch request must not fetch them
        mock_spotify_api.artist_top_tracks.assert_not_called()
        assert result.top_tracks == []

    def test_rejects_more_than_fifty_artists(self, mock_spotify_api):
        with pytest.raises(ValueError, match="Maximum 50"):
            get_artist([f"a{i}" for i in range(51)])

    def test_rejects_an_empty_list(self, mock_spotify_api):
        with pytest.raises(ValueError, match="At least one"):
            get_artist([])


class TestGetPlaylist:
    def test_success(self, mock_spotify_api, sample_playlist_data):
        mock_spotify_api.playlist.return_value = sample_playlist_data

        result = get_playlist("37i9dQZF1DX0XUsuxWHRQd")

        assert result.name == "RapCaviar"
        assert result.total_tracks == 50
        mock_spotify_api.playlist.assert_called_once_with(
            "37i9dQZF1DX0XUsuxWHRQd",
            fields="id,name,description,owner,public,tracks.total",
        )
        # a reported count is trusted, so no extra lookup is made
        mock_spotify_api._get.assert_not_called()

    def test_falls_back_to_the_items_endpoint_for_a_stripped_count(
        self, mock_spotify_api, sample_playlist_data
    ):
        """Restricted apps get `tracks.total` stripped; total_tracks must still fill."""
        mock_spotify_api.playlist.return_value = {**sample_playlist_data, "tracks": {}}
        mock_spotify_api._get.return_value = {"items": [], "total": 65}

        result = get_playlist("37i9dQZF1DX0XUsuxWHRQd")

        assert result.total_tracks == 65

    @pytest.mark.parametrize("reader", [get_playlist, playlist_resource])
    def test_forbidden_contents_preserve_readable_metadata(
        self, mock_spotify_api, sample_playlist_data, reader
    ):
        mock_spotify_api.playlist.return_value = {**sample_playlist_data, "tracks": {}}
        mock_spotify_api._get.side_effect = SpotifyException(403, -1, "Forbidden")

        result = reader("37i9dQZF1DX0XUsuxWHRQd")
        metadata = (
            json.loads(result) if isinstance(result, str) else result.model_dump()
        )

        assert metadata["id"] == sample_playlist_data["id"]
        assert metadata["name"] == sample_playlist_data["name"]
        assert metadata["total_tracks"] is None

    @pytest.mark.parametrize("status", [401, 429, 500])
    def test_count_lookup_errors_remain_visible(
        self, mock_spotify_api, sample_playlist_data, status
    ):
        mock_spotify_api.playlist.return_value = {**sample_playlist_data, "tracks": {}}
        error = SpotifyException(status, -1, "Request failed", reason="TEST_REASON")
        mock_spotify_api._get.side_effect = error

        with pytest.raises(ValueError) as raised:
            get_playlist("37i9dQZF1DX0XUsuxWHRQd")
        assert raised.value.__cause__ is error

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.playlist.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_playlist("badid")


class TestGetAlbum:
    def test_success(self, mock_spotify_api, sample_album_data):
        mock_spotify_api.album.return_value = sample_album_data

        result = get_album("6XzKGcM6laRkTrME3rQvJw")

        assert [a.name for a in result.albums] == ["Whenever You Need Somebody"]
        assert result.albums[0].label == "RCA"
        assert result.albums[0].total_tracks == 10
        assert len(result.tracks) == 1

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.album.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_album("badid")

    def test_batches_several_albums_into_one_request(
        self, mock_spotify_api, sample_album_data
    ):
        second = {**sample_album_data, "id": "al2", "name": "Second"}
        mock_spotify_api.albums.return_value = {"albums": [sample_album_data, second]}

        result = get_album(["6XzKGcM6laRkTrME3rQvJw", "al2"])

        assert [a.name for a in result.albums] == [
            "Whenever You Need Somebody",
            "Second",
        ]
        mock_spotify_api.albums.assert_called_once_with(
            ["6XzKGcM6laRkTrME3rQvJw", "al2"]
        )
        # the track list belongs to one album, so a batch request omits it
        assert result.tracks == []

    def test_rejects_more_than_twenty_albums(self, mock_spotify_api):
        # Spotify's album batch cap is 20, lower than the 50 for tracks/artists
        with pytest.raises(ValueError, match="Maximum 20"):
            get_album([f"al{i}" for i in range(21)])


class TestMembershipChecks:
    def test_saved_tracks_keyed_by_id(self, mock_spotify_api):
        mock_spotify_api._get.return_value = [True, False]

        result = check_saved_tracks(["abc", "spotify:track:def"])

        assert result.results == {"abc": True, "def": False}
        assert result.checked == 2
        mock_spotify_api._get.assert_called_once_with(
            "me/library/contains", uris="spotify:track:abc,spotify:track:def"
        )

    def test_saved_albums_cap_is_twenty(self, mock_spotify_api):
        with pytest.raises(ValueError, match="Maximum 20"):
            check_saved_albums([f"al{i}" for i in range(21)])

    def test_followed_artists_reads_the_library_route(self, mock_spotify_api):
        mock_spotify_api._get.return_value = [True]

        result = check_following_artists(["a1"])

        assert result.results == {"a1": True}
        mock_spotify_api._get.assert_called_once_with(
            "me/library/contains", uris="spotify:artist:a1"
        )

    def test_a_short_answer_is_an_error_not_a_mis_zip(self, mock_spotify_api):
        # Spotify answers positionally; a truncated answer must not silently
        # associate the wrong id with the wrong flag.
        mock_spotify_api._get.return_value = [True]

        with pytest.raises(ValueError):
            check_saved_tracks(["abc", "def"])

    def test_spotify_error_becomes_value_error(self, mock_spotify_api):
        mock_spotify_api._get.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            check_following_artists(["a1"])


class TestCreatePlaylist:
    def test_success(self, mock_spotify_api, sample_playlist_data):
        mock_spotify_api._post.return_value = sample_playlist_data

        result = create_playlist("My Playlist", description="desc", public=False)

        assert result.name == "RapCaviar"
        mock_spotify_api._post.assert_called_once_with(
            "me/playlists",
            payload={"name": "My Playlist", "public": False, "description": "desc"},
        )

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api._post.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            create_playlist("My Playlist")


class TestAddTracksToPlaylist:
    def test_converts_ids_and_uris(self, mock_spotify_api):
        mock_spotify_api._post.return_value = {"snapshot_id": "s1"}

        result = add_tracks_to_playlist("pl1", ["rawid", "spotify:track:already"])

        assert "Added 2 tracks" in result.message
        assert result.snapshot_id == "s1"
        mock_spotify_api._post.assert_called_once_with(
            "playlists/pl1/items",
            payload={"uris": ["spotify:track:rawid", "spotify:track:already"]},
        )

    def test_empty_list(self, mock_spotify_api):
        mock_spotify_api._post.return_value = {"snapshot_id": "s1"}

        result = add_tracks_to_playlist("pl1", [])

        assert "Added 0 tracks" in result.message

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api._post.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            add_tracks_to_playlist("pl1", ["rawid"])


class TestRemoveTracksFromPlaylist:
    async def test_converts_ids_and_uris(self, mock_spotify_api):
        mock_spotify_api._delete.return_value = {"snapshot_id": "s2"}

        result = await remove_tracks_from_playlist("pl1", ["rawid"])

        assert result.status == "success"
        assert result.snapshot_id == "s2"
        mock_spotify_api._delete.assert_called_once_with(
            "playlists/pl1/items",
            payload={"items": [{"uri": "spotify:track:rawid"}]},
        )

    async def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api._delete.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            await remove_tracks_from_playlist("pl1", ["rawid"])

    async def test_elicit_accept_proceeds(self, mock_spotify_api, mock_context):
        mock_spotify_api._delete.return_value = {"snapshot_id": "s2"}
        mock_spotify_api.playlist_remove_all_occurrences_of_items.return_value = {
            "snapshot_id": "s2"
        }
        mock_context.elicit.return_value = SimpleNamespace(
            action="accept", data=SimpleNamespace(confirm=True)
        )

        result = await remove_tracks_from_playlist("pl1", ["rawid"], ctx=mock_context)

        assert result.status == "success"
        mock_context.elicit.assert_awaited_once()
        mock_spotify_api._delete.assert_called_once()

    async def test_elicit_decline_cancels(self, mock_spotify_api, mock_context):
        mock_context.elicit.return_value = SimpleNamespace(action="decline", data=None)

        result = await remove_tracks_from_playlist("pl1", ["rawid"], ctx=mock_context)

        assert result.status == "cancelled"
        mock_spotify_api._delete.assert_not_called()

    async def test_elicit_unsupported_proceeds(self, mock_spotify_api, mock_context):
        mock_spotify_api._delete.return_value = {"snapshot_id": "s2"}
        # Client doesn't advertise elicitation: skip the prompt and proceed.
        mock_context.session.check_client_capability.return_value = False
        mock_spotify_api.playlist_remove_all_occurrences_of_items.return_value = {
            "snapshot_id": "s2"
        }

        result = await remove_tracks_from_playlist("pl1", ["rawid"], ctx=mock_context)

        assert result.status == "success"
        mock_context.elicit.assert_not_awaited()
        mock_spotify_api._delete.assert_called_once()

    async def test_elicit_error_does_not_delete(self, mock_spotify_api, mock_context):
        # Client supports elicitation but the prompt fails: must NOT delete.
        mock_context.elicit.side_effect = RuntimeError("transport failure")

        with pytest.raises(RuntimeError):
            await remove_tracks_from_playlist("pl1", ["rawid"], ctx=mock_context)

        mock_spotify_api._delete.assert_not_called()

    async def test_elicit_accept_without_confirm_cancels(
        self, mock_spotify_api, mock_context
    ):
        # accepting the form but leaving confirm unchecked must NOT delete
        mock_context.elicit.return_value = SimpleNamespace(
            action="accept", data=SimpleNamespace(confirm=False)
        )

        result = await remove_tracks_from_playlist("pl1", ["rawid"], ctx=mock_context)

        assert result.status == "cancelled"
        mock_spotify_api._delete.assert_not_called()


class TestUpdatePlaylistDetails:
    def test_success(self, mock_spotify_api):
        result = update_playlist_details("pl1", name="New Name", public=False)

        assert result.status == "success"
        mock_spotify_api.playlist_change_details.assert_called_once_with(
            "pl1", name="New Name", description=None, public=False
        )

    def test_success_with_description(self, mock_spotify_api):
        result = update_playlist_details("pl1", description="new desc")

        assert result.status == "success"
        mock_spotify_api.playlist_change_details.assert_called_once_with(
            "pl1", name=None, description="new desc", public=None
        )

    def test_no_fields_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="At least one"):
            update_playlist_details("pl1")

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.playlist_change_details.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            update_playlist_details("pl1", name="New Name")


class TestReorderPlaylist:
    def test_moves_block_and_returns_snapshot(self, mock_spotify_api):
        mock_spotify_api._put.return_value = {"snapshot_id": "s3"}

        result = reorder_playlist(
            "pl1", range_start=0, insert_before=10, range_length=3
        )

        assert result.status == "success"
        assert result.snapshot_id == "s3"
        mock_spotify_api._put.assert_called_once_with(
            "playlists/pl1/items",
            payload={"range_start": 0, "insert_before": 10, "range_length": 3},
        )

    def test_defaults_to_single_track(self, mock_spotify_api):
        mock_spotify_api._put.return_value = {"snapshot_id": "s3"}

        result = reorder_playlist("pl1", range_start=5, insert_before=0)

        assert "Moved 1 track" in result.message
        mock_spotify_api._put.assert_called_once_with(
            "playlists/pl1/items",
            payload={"range_start": 5, "insert_before": 0, "range_length": 1},
        )

    def test_passes_snapshot_id(self, mock_spotify_api):
        mock_spotify_api._put.return_value = {"snapshot_id": "s4"}

        reorder_playlist("pl1", range_start=1, insert_before=4, snapshot_id="prev")

        mock_spotify_api._put.assert_called_once_with(
            "playlists/pl1/items",
            payload={
                "range_start": 1,
                "insert_before": 4,
                "range_length": 1,
                "snapshot_id": "prev",
            },
        )

    def test_negative_position_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match=">= 0"):
            reorder_playlist("pl1", range_start=-1, insert_before=0)

    def test_zero_range_length_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="range_length"):
            reorder_playlist("pl1", range_start=0, insert_before=1, range_length=0)

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api._put.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            reorder_playlist("pl1", range_start=0, insert_before=1)


class TestListPlaylists:
    def test_success(self, mock_spotify_api, sample_playlist_data):
        mock_spotify_api.current_user_playlists.return_value = {
            "items": [sample_playlist_data],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }

        result = list_playlists()

        assert len(result.items) == 1
        assert isinstance(result.items[0], Playlist)
        assert result.items[0].name == "RapCaviar"
        mock_spotify_api.current_user_playlists.assert_called_once_with(
            limit=20, offset=0
        )

    def test_limit_clamped(self, mock_spotify_api):
        mock_spotify_api.current_user_playlists.return_value = {"items": []}

        list_playlists(limit=999)

        mock_spotify_api.current_user_playlists.assert_called_once_with(
            limit=50, offset=0
        )

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.current_user_playlists.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            list_playlists()


class TestGetPlaylistTracks:
    async def test_basic(self, mock_spotify_api, sample_track_data):
        mock_spotify_api._get.return_value = {
            "items": [{"track": sample_track_data}, {"track": sample_track_data}],
            "total": 2,
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 2}}

        result = await get_playlist_tracks("pl1", limit=50)

        assert len(result.items) == 2
        assert result.total == 2
        assert result.returned == 2
        mock_spotify_api._get.assert_called_with(
            "playlists/pl1/items", limit=50, offset=0
        )

    async def test_zero_limit_returns_no_tracks(
        self, mock_spotify_api, sample_track_data
    ):
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 20}}
        mock_spotify_api._get.return_value = {
            "items": [{"item": sample_track_data}],
            "next": None,
        }

        result = await get_playlist_tracks("pl1", limit=0)

        assert result.items == []
        assert result.total == 20
        mock_spotify_api._get.assert_not_called()

    async def test_stripped_metadata_uses_items_total_not_page_length(
        self, mock_spotify_api, sample_track_data
    ):
        # Restricted apps strip tracks.total; the items cursor still reports it
        mock_spotify_api.playlist.return_value = {}
        mock_spotify_api._get.return_value = {
            "items": [{"item": sample_track_data}],
            "total": 65,
            "next": "next-page",
        }

        result = await get_playlist_tracks("pl1", limit=1, offset=10)

        assert result.returned == 1
        assert result.offset == 10
        assert result.total == 65

    async def test_reads_entries_under_item_key(
        self, mock_spotify_api, sample_track_data
    ):
        # The Web API returns playlist entries under "item", not "track"
        mock_spotify_api._get.return_value = {
            "items": [{"item": sample_track_data}, {"item": sample_track_data}],
            "total": 2,
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 2}}

        result = await get_playlist_tracks("pl1", limit=50)

        assert result.returned == 2
        assert result.items[0].id == sample_track_data["id"]

    async def test_null_entries_returned_as_placeholders(
        self, mock_spotify_api, sample_track_data
    ):
        # A null entry keeps its slot so positions stay aligned with the playlist
        mock_spotify_api._get.return_value = {
            "items": [{"item": sample_track_data}, {"item": None, "is_local": True}],
            "total": 2,
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 2}}

        result = await get_playlist_tracks("pl1", limit=50)

        assert result.returned == 2
        assert result.items[1].id is None
        assert result.items[1].name == "Unavailable"
        assert result.items[1].is_local is True

    async def test_tracks_without_id_are_marked_not_dropped(
        self, mock_spotify_api, sample_track_data
    ):
        # Local files and unavailable/removed tracks come back with "id": None
        local_file = {**sample_track_data, "id": None, "is_local": True}
        mock_spotify_api._get.return_value = {
            "items": [{"item": sample_track_data}, {"item": local_file}],
            "total": 2,
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 2}}

        result = await get_playlist_tracks("pl1", limit=50)

        assert result.returned == 2
        assert result.items[0].id == sample_track_data["id"]
        assert result.items[1].id is None
        assert result.items[1].is_local is True
        assert result.items[1].name == sample_track_data["name"]

    async def test_limit_respected_when_no_entry_is_parseable(
        self, mock_spotify_api, sample_track_data
    ):
        # Regression: unparseable rows used to leave `remaining` untouched, so a
        # small limit paged through the whole playlist until the client timed out
        mock_spotify_api._get.return_value = {
            "items": [{"unexpected_key": sample_track_data}] * 5,
            "total": 1263,
            "next": "https://api.spotify.com/next",
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 1263}}

        result = await get_playlist_tracks("pl1", limit=5)

        assert mock_spotify_api._get.call_count == 1
        assert result.returned == 5
        assert all(t.id is None for t in result.items)

    async def test_spotify_error(self, mock_spotify_api):
        # Both regime shapes fail, so this is a real error rather than a fallback
        mock_spotify_api._get.side_effect = SPOTIFY_ERROR
        mock_spotify_api.playlist_tracks.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            await get_playlist_tracks("pl1", limit=50)

    async def test_reports_progress_with_context(
        self, mock_spotify_api, mock_context, sample_track_data
    ):
        mock_spotify_api._get.return_value = {
            "items": [{"track": sample_track_data}],
            "total": 1,
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 1}}

        await get_playlist_tracks("pl1", limit=50, ctx=mock_context)

        mock_context.report_progress.assert_awaited_with(progress=1, total=1)

    async def test_paginates_across_multiple_batches(
        self, mock_spotify_api, sample_track_data
    ):
        # limit=150 spans two API calls: 100 then the remaining 50
        batch1 = {
            "items": [{"track": sample_track_data}] * 100,
            "total": 150,
            "next": "https://api.spotify.com/next",
        }
        batch2 = {
            "items": [{"track": sample_track_data}] * 50,
            "total": 150,
            "next": None,
        }
        mock_spotify_api._get.side_effect = [batch1, batch2]
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 150}}

        result = await get_playlist_tracks("pl1", limit=150)

        assert result.returned == 150
        assert mock_spotify_api._get.call_count == 2
        first, second = mock_spotify_api._get.call_args_list
        assert first.args == ("playlists/pl1/items",)
        assert first.kwargs == {"limit": 100, "offset": 0}
        assert second.kwargs == {"limit": 50, "offset": 100}

    async def test_stops_when_batch_shorter_than_requested(
        self, mock_spotify_api, sample_track_data
    ):
        # next is set but the page came back short -> loop must still terminate
        mock_spotify_api._get.return_value = {
            "items": [{"track": sample_track_data}] * 3,
            "total": 500,
            "next": "https://api.spotify.com/next",
        }
        mock_spotify_api.playlist_items.return_value = {"total": 500}

        result = await get_playlist_tracks("pl1", limit=100)

        assert result.returned == 3
        assert mock_spotify_api._get.call_count == 1

    async def test_empty_playlist_returns_no_tracks(self, mock_spotify_api):
        mock_spotify_api._get.return_value = {"items": []}
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 0}}

        result = await get_playlist_tracks("pl1")

        assert result.returned == 0
        assert result.items == []

    async def test_total_falls_back_to_returned_count(
        self, mock_spotify_api, sample_track_data
    ):
        # playlist() omits tracks.total -> total should fall back to len(tracks)
        mock_spotify_api._get.return_value = {
            "items": [{"track": sample_track_data}],
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {}

        result = await get_playlist_tracks("pl1", limit=50)

        assert result.total == 1


class TestGetSavedTracks:
    def test_success_includes_added_at(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.current_user_saved_tracks.return_value = {
            "items": [{"track": sample_track_data, "added_at": "2024-01-01T00:00:00Z"}],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }

        result = get_saved_tracks()

        assert len(result.items) == 1
        assert result.items[0].added_at == "2024-01-01T00:00:00Z"
        assert result.items[0].name == "Never Gonna Give You Up"

    def test_limit_clamped(self, mock_spotify_api):
        mock_spotify_api.current_user_saved_tracks.return_value = {"items": []}

        get_saved_tracks(limit=999)

        mock_spotify_api.current_user_saved_tracks.assert_called_once_with(
            limit=50, offset=0
        )

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.current_user_saved_tracks.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_saved_tracks()


class TestResources:
    def test_current_user(self, mock_spotify_api):
        mock_spotify_api.current_user.return_value = {
            "id": "u1",
            "display_name": "Test User",
            "followers": {"total": 10},
            "country": "US",
            "product": "premium",
        }

        result = json.loads(current_user())

        assert result["id"] == "u1"
        assert result["product"] == "premium"

    def test_current_user_error_returns_json_error(self, mock_spotify_api):
        mock_spotify_api.current_user.side_effect = Exception("boom")

        result = json.loads(current_user())

        assert "error" in result

    def test_current_playback_resource(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_playback.return_value = sample_playback_data

        result = json.loads(current_playback_resource())

        assert result["is_playing"] is True
        assert result["track"]["name"] == "Never Gonna Give You Up"

    def test_current_playback_resource_no_playback(self, mock_spotify_api):
        mock_spotify_api.current_playback.return_value = None

        result = json.loads(current_playback_resource())

        assert result["status"] == "no_playback"

    def test_track_resource(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.track.return_value = sample_track_data

        result = json.loads(track_resource("4iV5W9uYEdYUVa79Axb7Rh"))

        assert result["name"] == "Never Gonna Give You Up"
        mock_spotify_api.track.assert_called_once_with("4iV5W9uYEdYUVa79Axb7Rh")

    def test_playlist_resource(self, mock_spotify_api, sample_playlist_data):
        mock_spotify_api.playlist.return_value = sample_playlist_data

        result = json.loads(playlist_resource("pl1"))

        assert result["name"] == "RapCaviar"

    def test_playlist_resource_fills_a_stripped_count(
        self, mock_spotify_api, sample_playlist_data
    ):
        mock_spotify_api.playlist.return_value = {**sample_playlist_data, "tracks": {}}
        mock_spotify_api._get.return_value = {"items": [], "total": 65}

        result = json.loads(playlist_resource("pl1"))

        assert result["total_tracks"] == 65

    def test_artist_resource(self, mock_spotify_api, sample_artist_data):
        mock_spotify_api.artist.return_value = sample_artist_data

        result = json.loads(artist_resource("a1"))

        assert result["name"] == "Rick Astley"

    def test_album_resource(self, mock_spotify_api, sample_album_data):
        mock_spotify_api.album.return_value = sample_album_data

        result = json.loads(album_resource("al1"))

        assert result["name"] == "Whenever You Need Somebody"

    def test_current_playback_resource_error(self, mock_spotify_api):
        mock_spotify_api.current_playback.side_effect = Exception("boom")

        result = json.loads(current_playback_resource())

        assert "error" in result

    @pytest.mark.parametrize(
        "resource_fn, api_attr",
        [
            (track_resource, "track"),
            (playlist_resource, "playlist"),
            (artist_resource, "artist"),
            (album_resource, "album"),
        ],
    )
    def test_resource_error_returns_json_error(
        self, mock_spotify_api, resource_fn, api_attr
    ):
        getattr(mock_spotify_api, api_attr).side_effect = Exception("boom")

        result = json.loads(resource_fn("badid"))

        assert "error" in result


class TestPrompts:
    def test_create_mood_playlist_includes_params(self):
        result = create_mood_playlist("energetic", genre="rock", decade="80s")

        assert "energetic" in result
        assert "rock" in result
        assert "80s" in result

    def test_analyze_large_playlist_includes_id(self):
        result = analyze_large_playlist("pl1", analysis_type="duplicates")

        assert "pl1" in result
        assert "duplicates" in result

    def test_discover_music_includes_query(self):
        result = discover_music_systematically("shoegaze", exploration_depth="deep")

        assert "shoegaze" in result
        assert "deep" in result


class TestGetMe:
    def test_returns_profile(self, mock_spotify_api):
        mock_spotify_api.current_user.return_value = {
            "id": "u1",
            "display_name": "Test User",
            "email": "t@example.com",
            "country": "US",
            "product": "premium",
            "followers": {"total": 10},
        }

        result = get_me()

        assert result.id == "u1"
        assert result.email == "t@example.com"
        assert result.followers == 10

    def test_survives_a_restricted_profile(self, mock_spotify_api):
        # Restricted apps get an id and nothing else; that must not error.
        mock_spotify_api.current_user.return_value = {"id": "u1"}

        result = get_me()

        assert result.id == "u1"
        assert result.email is None
        assert result.product is None


class TestSaveTracks:
    def test_saves(self, mock_spotify_api):
        result = save_tracks(["abc"])

        assert result.status == "success"
        mock_spotify_api._put.assert_called_once_with(
            "me/library", uris="spotify:track:abc"
        )

    def test_rejects_over_fifty(self, mock_spotify_api):
        with pytest.raises(ValueError, match="Maximum 50"):
            save_tracks([f"id{i}" for i in range(51)])


class TestRemoveSavedTracks:
    def test_removes(self, mock_spotify_api):
        result = remove_saved_tracks(["abc"])

        assert result.status == "success"
        mock_spotify_api._delete.assert_called_once_with(
            "me/library", uris="spotify:track:abc"
        )


class TestUnfollowPlaylist:
    def test_unfollows_by_uri(self, mock_spotify_api):
        result = unfollow_playlist("spotify:playlist:pl1")

        assert result.status == "success"
        mock_spotify_api._delete.assert_called_once_with(
            "me/library", uris="spotify:playlist:pl1"
        )


class TestGetRecentlyPlayed:
    def test_includes_played_at(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.current_user_recently_played.return_value = {
            "items": [{"track": sample_track_data, "played_at": "2026-07-01T00:00:00Z"}]
        }

        result = get_recently_played()

        assert result.items[0].played_at == "2026-07-01T00:00:00Z"

    def test_limit_clamped(self, mock_spotify_api):
        mock_spotify_api.current_user_recently_played.return_value = {"items": []}

        get_recently_played(limit=999)

        mock_spotify_api.current_user_recently_played.assert_called_once_with(limit=50)


class TestGetTopItems:
    def test_top_tracks(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.current_user_top_tracks.return_value = {
            "items": [sample_track_data]
        }

        result = get_top_items("tracks", time_range="short_term")

        assert result.time_range == "short_term"
        assert result.tracks is not None
        assert result.tracks[0].name == "Never Gonna Give You Up"
        assert result.artists is None

    def test_top_artists(self, mock_spotify_api, sample_artist_data):
        mock_spotify_api.current_user_top_artists.return_value = {
            "items": [sample_artist_data]
        }

        result = get_top_items("artists")

        assert result.artists is not None
        assert result.artists[0].followers == 1234567

    def test_rejects_bad_type(self, mock_spotify_api):
        with pytest.raises(ValueError, match="item_type"):
            get_top_items("albums")

    def test_rejects_bad_time_range(self, mock_spotify_api):
        with pytest.raises(ValueError, match="time_range"):
            get_top_items("tracks", time_range="yesterday")
