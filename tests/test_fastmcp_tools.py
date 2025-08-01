"""
Tests for FastMCP server tools.
"""

import pytest
from unittest.mock import patch, MagicMock
from spotipy import SpotifyException

# Import the FastMCP tools directly
from spotify_mcp.fastmcp_server import (
    playback_control, search_tracks, manage_queue, get_item_info,
    create_playlist, add_tracks_to_playlist, get_user_playlists,
    Track, PlaybackState, Playlist
)


class TestPlaybackControl:
    """Test playback control tool."""
    
    def test_get_playback_state(self, mock_spotify_api, sample_playback_data):
        """Test getting current playback state."""
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data
        
        result = playback_control("get")
        
        assert isinstance(result, PlaybackState)
        assert result.is_playing == True
        assert result.track is not None
        assert result.track.name == "Never Gonna Give You Up"
        mock_spotify_api.current_user_playing_track.assert_called_once()
    
    def test_start_playback(self, mock_spotify_api, sample_playback_data):
        """Test starting playback.""" 
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data
        
        result = playback_control("start")
        
        assert isinstance(result, PlaybackState)
        mock_spotify_api.start_playback.assert_called_once()
        mock_spotify_api.current_user_playing_track.assert_called()
    
    def test_pause_playback(self, mock_spotify_api, sample_playback_data):
        """Test pausing playback."""
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data
        
        result = playback_control("pause")
        
        assert isinstance(result, PlaybackState)
        mock_spotify_api.pause_playback.assert_called_once()
    
    def test_skip_track(self, mock_spotify_api, sample_playback_data):
        """Test skipping to next track."""
        mock_spotify_api.current_user_playing_track.return_value = sample_playback_data
        
        result = playback_control("skip")
        
        assert isinstance(result, PlaybackState)
        mock_spotify_api.next_track.assert_called_once()
    
    def test_invalid_action(self, mock_spotify_api):
        """Test invalid playback action."""
        with pytest.raises(ValueError, match="Invalid action"):
            playback_control("invalid_action")


class TestSearchTracks:
    """Test search functionality."""
    
    def test_basic_search(self, mock_spotify_api, sample_search_results):
        """Test basic track search."""
        mock_spotify_api.search.return_value = sample_search_results
        
        result = search_tracks("Never Gonna Give You Up")
        
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Track)
        assert result[0].name == "Never Gonna Give You Up"
        mock_spotify_api.search.assert_called_once_with(q="Never Gonna Give You Up", type="track", limit=10)
    
    def test_search_with_type(self, mock_spotify_api):
        """Test search with specific type."""
        artist_results = {
            "artists": {
                "items": [{
                    "id": "artist123",
                    "name": "Rick Astley",
                    "external_urls": {"spotify": "https://open.spotify.com/artist/artist123"}
                }]
            }
        }
        mock_spotify_api.search.return_value = artist_results
        
        result = search_tracks("Rick Astley", qtype="artist")
        
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].name == "Rick Astley"
        mock_spotify_api.search.assert_called_with(q="Rick Astley", type="artist", limit=10)
    
    def test_search_with_limit(self, mock_spotify_api, sample_search_results):
        """Test search with custom limit."""
        mock_spotify_api.search.return_value = sample_search_results
        
        result = search_tracks("test", limit=5)
        
        mock_spotify_api.search.assert_called_with(q="test", type="track", limit=5)
    
    def test_empty_results(self, mock_spotify_api):
        """Test search with no results."""
        empty_results = {"tracks": {"items": []}}
        mock_spotify_api.search.return_value = empty_results
        
        result = search_tracks("nonexistent")
        
        assert isinstance(result, list)
        assert len(result) == 0


class TestManageQueue:
    """Test queue management."""
    
    def test_add_to_queue(self, mock_spotify_api):
        """Test adding track to queue."""
        result = manage_queue("add", track_id="4iV5W9uYEdYUVa79Axb7Rh")
        
        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert "Added track to queue" in result["message"]
        mock_spotify_api.add_to_queue.assert_called_once_with("spotify:track:4iV5W9uYEdYUVa79Axb7Rh")
    
    def test_get_queue(self, mock_spotify_api, sample_track_data):
        """Test getting current queue."""
        queue_data = {
            "currently_playing": sample_track_data,
            "queue": [sample_track_data]
        }
        mock_spotify_api.queue.return_value = queue_data
        
        result = manage_queue("get")
        
        assert isinstance(result, dict)
        assert "currently_playing" in result
        assert "queue" in result
        assert isinstance(result["queue"], list)
        mock_spotify_api.queue.assert_called_once()
    
    def test_add_without_track_id(self, mock_spotify_api):
        """Test adding to queue without track ID."""
        with pytest.raises(ValueError, match="track_id required"):
            manage_queue("add")
    
    def test_invalid_action(self, mock_spotify_api):
        """Test invalid queue action."""
        with pytest.raises(ValueError, match="Invalid action"):
            manage_queue("invalid")


class TestGetItemInfo:
    """Test getting item information."""
    
    def test_get_track_info(self, mock_spotify_api, sample_track_data):
        """Test getting track information."""
        mock_spotify_api.track.return_value = sample_track_data
        
        result = get_item_info("4iV5W9uYEdYUVa79Axb7Rh", "track")
        
        assert isinstance(result, dict)  # Returns dict, not Track object
        assert result["name"] == "Never Gonna Give You Up" 
        assert result["artist"] == "Rick Astley"
        mock_spotify_api.track.assert_called_once_with("4iV5W9uYEdYUVa79Axb7Rh")
    
    def test_get_playlist_info(self, mock_spotify_api, sample_playlist_data):
        """Test getting playlist information."""
        mock_spotify_api.playlist.return_value = sample_playlist_data
        
        result = get_item_info("37i9dQZF1DX0XUsuxWHRQd", "playlist")
        
        assert isinstance(result, dict)  # Returns dict, not Playlist object
        assert result["name"] == "RapCaviar"
        mock_spotify_api.playlist.assert_called_once_with("37i9dQZF1DX0XUsuxWHRQd")
    
    def test_invalid_item_type(self, mock_spotify_api):
        """Test invalid item type."""
        with pytest.raises(ValueError, match="Unsupported item type"):
            get_item_info("invalid123", "invalid")


class TestCreatePlaylist:
    """Test playlist creation."""
    
    def test_create_basic_playlist(self, mock_spotify_api, sample_playlist_data):
        """Test creating a basic playlist."""
        mock_spotify_api.current_user.return_value = {"id": "testuser"}
        mock_spotify_api.user_playlist_create.return_value = sample_playlist_data
        
        result = create_playlist("My Test Playlist")
        
        assert isinstance(result, Playlist)
        assert result.name == "RapCaviar"  # from sample data
        mock_spotify_api.user_playlist_create.assert_called_once_with(
            "testuser", "My Test Playlist", public=True, description=""
        )
    
    def test_create_playlist_with_description(self, mock_spotify_api, sample_playlist_data):
        """Test creating playlist with description."""
        mock_spotify_api.current_user.return_value = {"id": "testuser"}
        mock_spotify_api.user_playlist_create.return_value = sample_playlist_data
        
        result = create_playlist("Test Playlist", description="Test description")
        
        mock_spotify_api.user_playlist_create.assert_called_with(
            "testuser", "Test Playlist", public=True, description="Test description"
        )


class TestAddTracksToPlaylist:
    """Test adding tracks to playlists."""
    
    def test_add_single_track(self, mock_spotify_api):
        """Test adding single track to playlist."""
        mock_spotify_api.playlist_add_items.return_value = {"snapshot_id": "test123"}
        
        result = add_tracks_to_playlist(
            "37i9dQZF1DX0XUsuxWHRQd", 
            ["4iV5W9uYEdYUVa79Axb7Rh"]
        )
        
        assert isinstance(result, dict)
        assert result["status"] == "success"
        assert "Added 1 tracks" in result["message"]
        mock_spotify_api.playlist_add_items.assert_called_once()
    
    def test_add_multiple_tracks(self, mock_spotify_api):
        """Test adding multiple tracks to playlist."""
        mock_spotify_api.playlist_add_items.return_value = {"snapshot_id": "test123"}
        tracks = ["4iV5W9uYEdYUVa79Axb7Rh", "5iV5W9uYEdYUVa79Axb7Ri"]
        
        result = add_tracks_to_playlist("37i9dQZF1DX0XUsuxWHRQd", tracks)
        
        assert isinstance(result, dict)
        assert "Added 2 tracks" in result["message"]
        mock_spotify_api.playlist_add_items.assert_called_once()
    
    def test_add_empty_track_list(self, mock_spotify_api):
        """Test adding empty track list."""
        result = add_tracks_to_playlist("37i9dQZF1DX0XUsuxWHRQd", [])
        
        # Should handle gracefully, not raise error
        assert isinstance(result, dict)
        assert "Added 0 tracks" in result["message"]


class TestGetUserPlaylists:
    """Test getting user playlists."""
    
    def test_get_playlists(self, mock_spotify_api, sample_playlist_data):
        """Test getting user playlists."""
        mock_spotify_api.current_user_playlists.return_value = {"items": [sample_playlist_data]}
        
        result = get_user_playlists()
        
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Playlist)
        assert result[0].name == "RapCaviar"
        mock_spotify_api.current_user_playlists.assert_called_once_with(limit=20)
    
    def test_get_playlists_with_limit(self, mock_spotify_api, sample_playlist_data):
        """Test getting playlists with limit."""
        mock_spotify_api.current_user_playlists.return_value = {"items": [sample_playlist_data]}
        
        result = get_user_playlists(limit=10)
        
        mock_spotify_api.current_user_playlists.assert_called_with(limit=10)


class TestErrorHandling:
    """Test error handling across tools."""
    
    def test_spotify_exception_handling(self, mock_spotify_api):
        """Test handling of Spotify API exceptions."""
        # Mock a Spotify API error
        mock_spotify_api.current_user_playing_track.side_effect = SpotifyException(
            404, -1, "No active device found"
        )
        
        # Should raise the handled error
        with pytest.raises(Exception):  # handle_spotify_error converts to different exception
            playback_control("get")
    
    def test_general_exception_handling(self, mock_spotify_api):
        """Test handling of general exceptions."""
        mock_spotify_api.search.side_effect = Exception("Network error")
        
        # Should propagate the exception
        with pytest.raises(Exception, match="Network error"):
            search_tracks("test query")