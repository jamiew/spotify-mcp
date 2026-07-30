"""
FastMCP test configuration and fixtures.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spotify_mcp import spotify_api


@pytest.fixture(autouse=True)
def reset_regime_cache():
    """`with_fallback` caches the resolved regime per family for the life of the
    process, so without this one test's fallback pins every later one."""
    spotify_api._legacy_families.clear()
    yield
    spotify_api._legacy_families.clear()


# Sample Spotify API response data for testing
SAMPLE_TRACK = {
    "id": "4iV5W9uYEdYUVa79Axb7Rh",
    "name": "Never Gonna Give You Up",
    "artists": [{"name": "Rick Astley", "id": "0gxyHStUsqpMadRV0Di1Qt"}],
    "album": {"name": "Whenever You Need Somebody", "id": "6XzKGcM6laRkTrME3rQvJw"},
    "duration_ms": 213573,
    "popularity": 85,
    "external_urls": {
        "spotify": "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"
    },
}

SAMPLE_PLAYLIST = {
    "id": "37i9dQZF1DX0XUsuxWHRQd",
    "name": "RapCaviar",
    "description": "New music from hip-hop's underground",
    "owner": {"display_name": "Spotify", "id": "spotify"},
    "tracks": {"total": 50},
    "external_urls": {
        "spotify": "https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd"
    },
}

SAMPLE_PLAYBACK_STATE = {
    "is_playing": True,
    "item": SAMPLE_TRACK,
    "device": {"name": "My iPhone", "volume_percent": 70},
    "shuffle_state": False,
    "repeat_state": "off",
    "progress_ms": 60000,
}

SAMPLE_SEARCH_RESULTS = {
    "tracks": {
        "items": [SAMPLE_TRACK],
        "total": 1,
        "limit": 10,
        "offset": 0,
        "next": None,
        "previous": None,
    },
    "albums": {
        "items": [],
        "total": 0,
        "limit": 10,
        "offset": 0,
        "next": None,
        "previous": None,
    },
    "artists": {
        "items": [],
        "total": 0,
        "limit": 10,
        "offset": 0,
        "next": None,
        "previous": None,
    },
    "playlists": {
        "items": [],
        "total": 0,
        "limit": 10,
        "offset": 0,
        "next": None,
        "previous": None,
    },
}


SAMPLE_ARTIST = {
    "id": "0gxyHStUsqpMadRV0Di1Qt",
    "name": "Rick Astley",
    "genres": ["dance pop", "new wave pop"],
    "popularity": 78,
    "followers": {"total": 1234567},
}

SAMPLE_ALBUM = {
    "id": "6XzKGcM6laRkTrME3rQvJw",
    "name": "Whenever You Need Somebody",
    "artists": [{"name": "Rick Astley", "id": "0gxyHStUsqpMadRV0Di1Qt"}],
    "release_date": "1987-11-12",
    "release_date_precision": "day",
    "total_tracks": 10,
    "album_type": "album",
    "label": "RCA",
    "genres": [],
    "popularity": 70,
    "external_urls": {
        "spotify": "https://open.spotify.com/album/6XzKGcM6laRkTrME3rQvJw"
    },
    "tracks": {"items": [SAMPLE_TRACK]},
}


@pytest.fixture
def mock_spotify_client():
    """Create a mocked Spotify client for testing."""
    mock_client = MagicMock()

    # Mock common methods
    mock_client.current_playback.return_value = SAMPLE_PLAYBACK_STATE
    mock_client.search.return_value = SAMPLE_SEARCH_RESULTS
    mock_client.user_playlists.return_value = {"items": [SAMPLE_PLAYLIST]}
    mock_client.playlist.return_value = SAMPLE_PLAYLIST
    mock_client.track.return_value = SAMPLE_TRACK
    mock_client.playlist_add_items.return_value = {"snapshot_id": "test123"}
    mock_client.user_playlist_create.return_value = SAMPLE_PLAYLIST

    return mock_client


@pytest.fixture
def mock_spotify_api(mock_spotify_client):
    """Mock the spotify_api module."""
    with patch("spotify_mcp.fastmcp_server.spotify_client", mock_spotify_client):
        yield mock_spotify_client


@pytest.fixture
def mock_context():
    """A mock MCP Context with async progress/log/elicit methods.

    Defaults to a client that supports elicitation; tests that exercise the
    unsupported path flip check_client_capability to return False.
    """
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    ctx.info = AsyncMock()
    ctx.elicit = AsyncMock()
    ctx.session.check_client_capability = MagicMock(return_value=True)
    return ctx


@pytest.fixture
def sample_track_data():
    """Provide sample track data for tests."""
    return SAMPLE_TRACK


@pytest.fixture
def sample_playlist_data():
    """Provide sample playlist data for tests."""
    return SAMPLE_PLAYLIST


@pytest.fixture
def sample_playback_data():
    """Provide sample playback state data for tests."""
    return SAMPLE_PLAYBACK_STATE


@pytest.fixture
def sample_search_results():
    """Provide sample search results for tests."""
    return SAMPLE_SEARCH_RESULTS


@pytest.fixture
def sample_artist_data():
    """Provide sample artist data for tests."""
    return SAMPLE_ARTIST


@pytest.fixture
def sample_album_data():
    """Provide sample album data for tests."""
    return SAMPLE_ALBUM
