"""Tests for FastMCP server tools, resources, and prompts.

External Spotify calls are mocked at the spotipy-client boundary only; every
test asserts on the real transformation/validation logic in the tools.
"""

import json

import pytest
from spotipy import SpotifyException

from spotify_mcp.fastmcp_server import (
    PlaybackState,
    Playlist,
    Track,
    add_to_queue,
    add_tracks_to_playlist,
    analyze_large_playlist,
    create_mood_playlist,
    create_playlist,
    current_playback_resource,
    current_user,
    discover_music_systematically,
    get_album_info,
    get_artist_info,
    get_playlist_info,
    get_playlist_tracks,
    get_queue,
    get_saved_tracks,
    get_track_info,
    get_user_playlists,
    modify_playlist_details,
    playback_control,
    remove_tracks_from_playlist,
    search_tracks,
)

# A SpotifyException the tools should translate into a ValueError.
SPOTIFY_ERROR = SpotifyException(404, -1, "track not found")


class TestPlaybackControl:
    def test_get_playback_state(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data

        result = playback_control("get")

        assert isinstance(result, PlaybackState)
        assert result.is_playing
        assert result.track is not None
        assert result.track.name == "Never Gonna Give You Up"
        assert result.device == "My iPhone"
        assert result.volume == 70
        mock_spotify_api.current_user_playing_track.assert_called_once()

    def test_start_playback_with_track(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data

        playback_control("start", track_id="4iV5W9uYEdYUVa79Axb7Rh")

        mock_spotify_api.start_playback.assert_called_once_with(
            uris=["spotify:track:4iV5W9uYEdYUVa79Axb7Rh"]
        )

    def test_start_playback_resume(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data

        playback_control("start")

        mock_spotify_api.start_playback.assert_called_once_with()

    def test_pause_playback(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data

        playback_control("pause")

        mock_spotify_api.pause_playback.assert_called_once()

    def test_skip_multiple_tracks(self, mock_spotify_api, sample_playback_data):
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data

        playback_control("skip", num_skips=3)

        assert mock_spotify_api.next_track.call_count == 3

    def test_invalid_action_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="Invalid action"):
            playback_control("invalid_action")

    def test_get_with_no_active_playback(self, mock_spotify_api):
        mock_spotify_api.current_user_playing_track.return_value = None

        result = playback_control("get")

        assert isinstance(result, PlaybackState)
        assert result.is_playing is False
        assert result.track is None

    def test_spotify_error_becomes_value_error(self, mock_spotify_api):
        mock_spotify_api.current_user_playing_track.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            playback_control("get")


class TestSearchTracks:
    def test_basic_track_search(self, mock_spotify_api, sample_search_results):
        mock_spotify_api.search.return_value = sample_search_results

        result = search_tracks("Never Gonna Give You Up")

        assert len(result["items"]) == 1
        assert isinstance(result["items"][0], Track)
        assert result["items"][0].name == "Never Gonna Give You Up"
        assert result["total"] == 1
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

        result = search_tracks("Rick Astley", qtype="artist")

        assert result["items"][0].name == "Rick Astley"

    def test_search_builds_filtered_query(
        self, mock_spotify_api, sample_search_results
    ):
        mock_spotify_api.search.return_value = sample_search_results

        search_tracks("love", year="2024", genre="pop", artist="Foo")

        mock_spotify_api.search.assert_called_once_with(
            q="love artist:Foo year:2024 genre:pop", type="track", limit=10, offset=0
        )

    def test_limit_is_clamped(self, mock_spotify_api, sample_search_results):
        mock_spotify_api.search.return_value = sample_search_results

        search_tracks("test", limit=999)

        mock_spotify_api.search.assert_called_once_with(
            q="test", type="track", limit=50, offset=0
        )

    def test_empty_results(self, mock_spotify_api):
        mock_spotify_api.search.return_value = {
            "tracks": {"items": [], "total": 0, "limit": 10, "offset": 0}
        }

        result = search_tracks("nonexistent")

        assert result["items"] == []
        assert result["total"] == 0

    def test_spotify_error_becomes_value_error(self, mock_spotify_api):
        mock_spotify_api.search.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            search_tracks("test")


class TestQueue:
    def test_add_to_queue_converts_id_to_uri(self, mock_spotify_api):
        result = add_to_queue("4iV5W9uYEdYUVa79Axb7Rh")

        assert result["status"] == "success"
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

        assert result["currently_playing"]["name"] == "Never Gonna Give You Up"
        assert len(result["queue"]) == 2

    def test_get_queue_empty(self, mock_spotify_api):
        mock_spotify_api.queue.return_value = {"currently_playing": None, "queue": []}

        result = get_queue()

        assert result["currently_playing"] is None
        assert result["queue"] == []

    def test_get_queue_error(self, mock_spotify_api):
        mock_spotify_api.queue.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_queue()


class TestGetTrackInfo:
    def test_single_track(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.track.return_value = sample_track_data

        result = get_track_info("4iV5W9uYEdYUVa79Axb7Rh")

        assert len(result["tracks"]) == 1
        assert result["tracks"][0]["artist"] == "Rick Astley"
        mock_spotify_api.track.assert_called_once_with("4iV5W9uYEdYUVa79Axb7Rh")

    def test_batch_tracks(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.tracks.return_value = {
            "tracks": [sample_track_data, sample_track_data]
        }

        result = get_track_info(["id1", "id2"])

        assert len(result["tracks"]) == 2
        mock_spotify_api.tracks.assert_called_once_with(["id1", "id2"])

    def test_too_many_ids_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="Maximum 50"):
            get_track_info([f"id{i}" for i in range(51)])

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.track.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_track_info("badid")


class TestGetArtistInfo:
    def test_success(self, mock_spotify_api, sample_artist_data, sample_track_data):
        mock_spotify_api.artist.return_value = sample_artist_data
        mock_spotify_api.artist_top_tracks.return_value = {
            "tracks": [sample_track_data]
        }

        result = get_artist_info("0gxyHStUsqpMadRV0Di1Qt")

        assert result["artist"]["name"] == "Rick Astley"
        assert result["artist"]["followers"] == 1234567
        assert result["artist"]["genres"] == ["dance pop", "new wave pop"]
        assert len(result["top_tracks"]) == 1
        assert result["top_tracks"][0]["name"] == "Never Gonna Give You Up"

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.artist.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_artist_info("badid")


class TestGetPlaylistInfo:
    def test_success(self, mock_spotify_api, sample_playlist_data):
        mock_spotify_api.playlist.return_value = sample_playlist_data

        result = get_playlist_info("37i9dQZF1DX0XUsuxWHRQd")

        assert result["name"] == "RapCaviar"
        assert result["total_tracks"] == 50
        mock_spotify_api.playlist.assert_called_once_with(
            "37i9dQZF1DX0XUsuxWHRQd",
            fields="id,name,description,owner,public,tracks.total",
        )

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.playlist.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_playlist_info("badid")


class TestGetAlbumInfo:
    def test_success(self, mock_spotify_api, sample_album_data):
        mock_spotify_api.album.return_value = sample_album_data

        result = get_album_info("6XzKGcM6laRkTrME3rQvJw")

        assert result["album"]["name"] == "Whenever You Need Somebody"
        assert result["album"]["label"] == "RCA"
        assert result["album"]["total_tracks"] == 10
        assert len(result["tracks"]) == 1

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.album.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_album_info("badid")


class TestCreatePlaylist:
    def test_success(self, mock_spotify_api, sample_playlist_data):
        mock_spotify_api.current_user.return_value = {"id": "testuser"}
        mock_spotify_api.user_playlist_create.return_value = sample_playlist_data

        result = create_playlist("My Playlist", description="desc", public=False)

        assert result["name"] == "RapCaviar"
        mock_spotify_api.user_playlist_create.assert_called_once_with(
            "testuser", "My Playlist", public=False, description="desc"
        )

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.current_user.return_value = {"id": "testuser"}
        mock_spotify_api.user_playlist_create.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            create_playlist("My Playlist")


class TestAddTracksToPlaylist:
    def test_converts_ids_and_uris(self, mock_spotify_api):
        mock_spotify_api.playlist_add_items.return_value = {"snapshot_id": "s1"}

        result = add_tracks_to_playlist("pl1", ["rawid", "spotify:track:already"])

        assert "Added 2 tracks" in result["message"]
        mock_spotify_api.playlist_add_items.assert_called_once_with(
            "pl1", ["spotify:track:rawid", "spotify:track:already"]
        )

    def test_empty_list(self, mock_spotify_api):
        mock_spotify_api.playlist_add_items.return_value = {"snapshot_id": "s1"}

        result = add_tracks_to_playlist("pl1", [])

        assert "Added 0 tracks" in result["message"]

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.playlist_add_items.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            add_tracks_to_playlist("pl1", ["rawid"])


class TestRemoveTracksFromPlaylist:
    def test_converts_ids_and_uris(self, mock_spotify_api):
        result = remove_tracks_from_playlist("pl1", ["rawid"])

        assert result["status"] == "success"
        mock_spotify_api.playlist_remove_all_occurrences_of_items.assert_called_once_with(
            "pl1", ["spotify:track:rawid"]
        )

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.playlist_remove_all_occurrences_of_items.side_effect = (
            SPOTIFY_ERROR
        )

        with pytest.raises(ValueError):
            remove_tracks_from_playlist("pl1", ["rawid"])


class TestModifyPlaylistDetails:
    def test_success(self, mock_spotify_api):
        result = modify_playlist_details("pl1", name="New Name", public=False)

        assert result["status"] == "success"
        mock_spotify_api.playlist_change_details.assert_called_once_with(
            "pl1", name="New Name", description=None, public=False
        )

    def test_no_fields_raises(self, mock_spotify_api):
        with pytest.raises(ValueError, match="At least one"):
            modify_playlist_details("pl1")

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.playlist_change_details.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            modify_playlist_details("pl1", name="New Name")


class TestGetUserPlaylists:
    def test_success(self, mock_spotify_api, sample_playlist_data):
        mock_spotify_api.current_user_playlists.return_value = {
            "items": [sample_playlist_data],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }

        result = get_user_playlists()

        assert len(result["items"]) == 1
        assert isinstance(result["items"][0], Playlist)
        assert result["items"][0].name == "RapCaviar"
        mock_spotify_api.current_user_playlists.assert_called_once_with(
            limit=20, offset=0
        )

    def test_limit_clamped(self, mock_spotify_api):
        mock_spotify_api.current_user_playlists.return_value = {"items": []}

        get_user_playlists(limit=999)

        mock_spotify_api.current_user_playlists.assert_called_once_with(
            limit=50, offset=0
        )

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.current_user_playlists.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_user_playlists()


class TestGetPlaylistTracks:
    def test_basic(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.playlist_tracks.return_value = {
            "items": [{"track": sample_track_data}, {"track": sample_track_data}],
            "total": 2,
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 2}}

        result = get_playlist_tracks("pl1", limit=50)

        assert len(result["items"]) == 2
        assert result["total"] == 2
        assert result["returned"] == 2
        mock_spotify_api.playlist_tracks.assert_called_with("pl1", limit=50, offset=0)

    def test_skips_null_track_items(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.playlist_tracks.return_value = {
            "items": [{"track": sample_track_data}, {"track": None}],
            "total": 2,
            "next": None,
        }
        mock_spotify_api.playlist.return_value = {"tracks": {"total": 2}}

        result = get_playlist_tracks("pl1", limit=50)

        assert result["returned"] == 1

    def test_spotify_error(self, mock_spotify_api):
        mock_spotify_api.playlist_tracks.side_effect = SPOTIFY_ERROR

        with pytest.raises(ValueError):
            get_playlist_tracks("pl1", limit=50)


class TestGetSavedTracks:
    def test_success_includes_added_at(self, mock_spotify_api, sample_track_data):
        mock_spotify_api.current_user_saved_tracks.return_value = {
            "items": [{"track": sample_track_data, "added_at": "2024-01-01T00:00:00Z"}],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }

        result = get_saved_tracks()

        assert len(result["items"]) == 1
        assert result["items"][0]["added_at"] == "2024-01-01T00:00:00Z"
        assert result["items"][0]["name"] == "Never Gonna Give You Up"

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
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data

        result = json.loads(current_playback_resource())

        assert result["is_playing"] is True
        assert result["track"]["name"] == "Never Gonna Give You Up"

    def test_current_playback_resource_no_playback(self, mock_spotify_api):
        mock_spotify_api.current_user_playing_track.return_value = None

        result = json.loads(current_playback_resource())

        assert result["status"] == "no_playback"


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
