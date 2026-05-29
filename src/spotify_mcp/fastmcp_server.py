"""
Modern FastMCP-based Spotify MCP Server.
Clean, simple implementation using FastMCP's automatic features.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import TYPE_CHECKING, cast

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import (
    ClientCapabilities,
    ElicitationCapability,
    Icon,
    ToolAnnotations,
)
from pydantic import BaseModel
from spotipy import SpotifyException

import spotify_mcp.spotify_api as spotify_api
from spotify_mcp.errors import convert_spotify_error
from spotify_mcp.logging_utils import (
    log_pagination_info,
    log_tool_execution,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from spotify_mcp.spotify_types import (
        AlbumObject,
        AlbumRef,
        ArtistObject,
        PlaylistObject,
        TrackObject,
    )

# Configure logging
logger = logging.getLogger(__name__)

# Configure structured logging for better observability
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# Create FastMCP app
mcp = FastMCP("Spotify MCP")

# Shared Spotify glyph (inline data URI) attached to tools/resources/prompts.
SPOTIFY_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PGNpcmNsZSBjeD0iMTIiIGN5PSIxMiIgcj0iMTIiIGZpbGw9IiMxREI5NTQiLz48cGF0aCBmaWxsPSIjZmZmIiBkPSJNMTcgMTYuNmEuNy43IDAgMCAxLTEgLjI1Yy0yLjctMS42NS02LjEtMi0xMC4xLTEuMWEuNzUuNzUgMCAxIDEtLjMzLTEuNDZjNC40LTEgOC4yLS42IDExLjIgMS4yNS4zNS4yLjQ2LjY2LjIzIDEuMDZ6bTEuMy0yLjk1YS45NC45NCAwIDAgMS0xLjI5LjNjLTMuMS0xLjktNy44LTIuNDYtMTEuNDUtMS4zNWEuOTQuOTQgMCAxIDEtLjU1LTEuOGM0LjE4LTEuMjcgOS4zNi0uNjUgMTIuOTMgMS41NS40NC4yNy41OC44NS4zNiAxLjN6bS4xLTMuMDdDMTQuNyA4LjQgOC45IDguMiA1LjQzIDkuMjZhMS4xMiAxLjEyIDAgMSAxLS42NS0yLjE1QzguNzYgNS45IDE1LjE4IDYuMTMgMTkuNDUgOC42NmExLjEyIDEuMTIgMCAxIDEtMS4xNSAxLjkyeiIvPjwvc3ZnPg==",
    mimeType="image/svg+xml",
)

# Initialize Spotify client
_client_wrapper = spotify_api.Client()
spotify_client = _client_wrapper.sp  # Use spotipy client directly


# Data models for structured output
class Track(BaseModel):
    """A Spotify track with metadata."""

    name: str
    id: str
    artist: str
    artists: list[str] | None = None
    album: str | None = None
    album_id: str | None = None
    release_date: str | None = None
    duration_ms: int | None = None
    popularity: int | None = None
    external_urls: dict[str, str] | None = None
    added_at: str | None = None


class PlaybackState(BaseModel):
    """Current playback state."""

    is_playing: bool
    track: Track | None = None
    device: str | None = None
    volume: int | None = None
    shuffle: bool = False
    repeat: str = "off"
    progress_ms: int | None = None


class Playlist(BaseModel):
    """A Spotify playlist."""

    name: str
    id: str
    owner: str | None = None
    description: str | None = None
    tracks: list[Track] | None = None
    total_tracks: int | None = None
    public: bool | None = None


class Artist(BaseModel):
    """A Spotify artist."""

    name: str
    id: str
    genres: list[str] | None = None
    popularity: int | None = None
    followers: int | None = None


class Album(BaseModel):
    """A Spotify album."""

    name: str
    id: str
    artist: str
    artists: list[str] | None = None
    release_date: str | None = None
    release_date_precision: str | None = None
    total_tracks: int | None = None
    album_type: str | None = None
    label: str | None = None
    genres: list[str] | None = None
    popularity: int | None = None
    external_urls: dict[str, str] | None = None


# Tool result models (give every tool a real output schema)
class SearchResults(BaseModel):
    """Paginated search results."""

    items: list[Track]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class QueueState(BaseModel):
    """Currently playing track plus the upcoming queue."""

    currently_playing: Track | None = None
    queue: list[Track]


class TrackList(BaseModel):
    """A list of tracks."""

    tracks: list[Track]


class ArtistInfo(BaseModel):
    """An artist with their top tracks."""

    artist: Artist
    top_tracks: list[Track]


class AlbumInfo(BaseModel):
    """An album with its tracks."""

    album: Album
    tracks: list[Track]


class PlaylistList(BaseModel):
    """Paginated list of playlists."""

    items: list[Playlist]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class PlaylistTracks(BaseModel):
    """Paginated tracks from a single playlist."""

    items: list[Track]
    total: int
    limit: int | None = None
    offset: int
    returned: int


class SavedTracks(BaseModel):
    """Paginated saved/liked tracks."""

    items: list[Track]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class ActionResult(BaseModel):
    """Result of a state-changing operation."""

    status: str
    message: str
    snapshot_id: str | None = None


class RemovalConfirmation(BaseModel):
    """Elicitation schema confirming a destructive playlist edit."""

    confirm: bool = False


def parse_track(item: TrackObject) -> Track:
    """Parse Spotify track data into Track model."""
    album_data = item.get("album", {})
    artists = item.get("artists", [])
    return Track(
        name=item["name"],
        id=item["id"],
        artist=artists[0]["name"] if artists else "Unknown",
        artists=[a["name"] for a in artists],
        album=album_data.get("name"),
        album_id=album_data.get("id"),
        release_date=album_data.get("release_date"),
        duration_ms=item.get("duration_ms"),
        popularity=item.get("popularity"),
        external_urls=cast("dict[str, str]", item.get("external_urls")),
    )


async def get_playlist_tracks_paginated(
    playlist_id: str,
    limit: int | None = None,
    offset: int = 0,
    ctx: Context | None = None,
    total: int | None = None,
) -> list[Track]:
    """Get playlist tracks with proper pagination support.
    Args:
        playlist_id: Spotify playlist ID
        limit: Maximum number of tracks to return (None for all)
        offset: Number of tracks to skip
        ctx: Optional MCP context for progress + log notifications
        total: Total track count, used as the progress denominator

    Returns:
        List of Track objects
    """
    tracks = []
    current_offset = offset
    batch_size = min(limit, 100) if limit else 100  # Spotify API max is 100 per request
    remaining = limit

    logger.info(
        f"📄 Starting paginated fetch for playlist {playlist_id} (limit={limit}, offset={offset})"
    )

    while True:
        # Determine how many to fetch in this batch
        batch_limit = min(batch_size, remaining) if remaining else batch_size

        logger.info(f"📄 Fetching batch: offset={current_offset}, limit={batch_limit}")
        # Get playlist tracks with pagination
        tracks_result = spotify_client.playlist_tracks(
            playlist_id, limit=batch_limit, offset=current_offset
        )

        if not tracks_result or not tracks_result.get("items"):
            break

        # Parse and add tracks
        batch_tracks = []
        for item in tracks_result["items"]:
            if item and item.get("track"):
                batch_tracks.append(parse_track(item["track"]))

        tracks.extend(batch_tracks)
        logger.info(
            f"📄 Batch complete: retrieved {len(batch_tracks)} tracks (total so far: {len(tracks)})"
        )

        if ctx is not None:
            await ctx.report_progress(progress=len(tracks), total=total)
            await ctx.info(f"Fetched {len(tracks)} tracks so far")

        # Update remaining count if we have a limit
        if remaining:
            remaining -= len(batch_tracks)
            if remaining <= 0:
                break

        # Check if we've reached the end
        if len(tracks_result["items"]) < batch_limit or not tracks_result.get("next"):
            break

        current_offset += len(tracks_result["items"])

        # Safety check to prevent infinite loops
        if current_offset > 10000:
            logger.warning(
                f"⚠️ Safety limit reached: stopping at offset {current_offset}"
            )
            break

    logger.info(f"📄 Pagination complete: total {len(tracks)} tracks retrieved")
    return tracks


# === TOOLS ===


@mcp.tool(
    title="Control Playback",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def playback_control(
    action: str, track_id: str | None = None, num_skips: int = 1
) -> PlaybackState:
    """Control Spotify playback.

    Args:
        action: Action ('get', 'start', 'pause', 'skip')
        track_id: Track ID to play (for 'start')
        num_skips: Number of tracks to skip
    """
    try:
        if action == "get":
            logger.info("🎵 Getting current playback state")
            result = spotify_client.current_playback()
        elif action == "start":
            if track_id:
                logger.info(f"🎵 Starting playback of track: {track_id}")
                spotify_client.start_playback(uris=[f"spotify:track:{track_id}"])
            else:
                logger.info("🎵 Resuming playback")
                spotify_client.start_playback()
            result = spotify_client.current_playback()
        elif action == "pause":
            logger.info("🎵 Pausing playback")
            spotify_client.pause_playback()
            result = spotify_client.current_playback()
        elif action == "skip":
            logger.info(f"🎵 Skipping {num_skips} track(s)")
            for _ in range(num_skips):
                spotify_client.next_track()
            result = spotify_client.current_playback()
        else:
            raise ValueError(f"Invalid action: {action}")

        # Parse result
        track = None
        if result and result.get("item"):
            track = parse_track(result["item"])

        return PlaybackState(
            is_playing=result.get("is_playing", False) if result else False,
            track=track,
            device=result.get("device", {}).get("name")
            if result and result.get("device")
            else None,
            volume=result.get("device", {}).get("volume_percent")
            if result and result.get("device")
            else None,
            shuffle=result.get("shuffle_state", False) if result else False,
            repeat=result.get("repeat_state", "off") if result else "off",
            progress_ms=result.get("progress_ms") if result else None,
        )

    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Search Spotify",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def search_tracks(
    query: str,
    qtype: str = "track",
    limit: int = 10,
    offset: int = 0,
    year: str | None = None,
    year_range: str | None = None,
    genre: str | None = None,
    artist: str | None = None,
    album: str | None = None,
) -> SearchResults:
    """Search Spotify for tracks, albums, artists, or playlists.

    Args:
        query: Search query
        qtype: Type ('track', 'album', 'artist', 'playlist')
        limit: Max results per page (1-50, default 10)
        offset: Number of results to skip for pagination (default 0)
        year: Filter by year (e.g., '2024')
        year_range: Filter by year range (e.g., '2020-2024')
        genre: Filter by genre (e.g., 'electronic', 'hip-hop')
        artist: Filter by artist name
        album: Filter by album name

    Returns:
        SearchResults with 'items' (list of tracks) and pagination info ('total', 'limit', 'offset')

    Note: Filters use Spotify's search syntax. For large result sets, use offset to paginate.
    Example: query='love', year='2024', genre='pop' searches for 'love year:2024 genre:pop'
    """
    try:
        limit = max(1, min(50, limit))

        # Build filtered query
        filters = []
        if artist:
            filters.append(f"artist:{artist}")
        if album:
            filters.append(f"album:{album}")
        if year:
            filters.append(f"year:{year}")
        if year_range:
            filters.append(f"year:{year_range}")
        if genre:
            filters.append(f"genre:{genre}")

        full_query = " ".join([query] + filters) if filters else query

        logger.info(
            f"🔍 Searching {qtype}s: '{full_query}' (limit={limit}, offset={offset})"
        )
        result = spotify_client.search(
            q=full_query, type=qtype, limit=limit, offset=offset
        )

        tracks = []
        items_key = f"{qtype}s"
        result_section = result.get(items_key, {})
        # Spotify can return null entries in items (e.g. removed content), so guard each.
        if qtype == "track" and result_section.get("items"):
            tracks = [parse_track(item) for item in result_section["items"] if item]
        else:
            # Convert other types to track-like format for consistency
            if result_section.get("items"):
                for item in result_section["items"]:
                    if not item:
                        continue
                    track = Track(
                        name=item["name"],
                        id=item["id"],
                        artist=item.get("artists", [{}])[0].get("name", "Unknown")
                        if qtype != "artist"
                        else item["name"],
                        external_urls=item.get("external_urls"),
                    )
                    tracks.append(track)

        total_results = result_section.get("total", 0)
        logger.info(
            f"🔍 Search returned {len(tracks)} items (total available: {total_results})"
        )
        log_pagination_info("search_tracks", total_results, limit, offset)

        return SearchResults(
            items=tracks,
            total=total_results,
            limit=result_section.get("limit", limit),
            offset=result_section.get("offset", offset),
            next=result_section.get("next"),
            previous=result_section.get("previous"),
        )
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Add to Queue",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def add_to_queue(track_id: str) -> ActionResult:
    """Add a track to the playback queue.

    Args:
        track_id: Spotify track ID to add to queue
    Returns:
        Status and message
    """
    try:
        logger.info(f"🎵 Adding track {track_id} to queue")
        spotify_client.add_to_queue(f"spotify:track:{track_id}")
        return ActionResult(status="success", message="Added track to queue")
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Get Queue",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def get_queue() -> QueueState:
    """Get the current playback queue.
    Returns:
        Currently playing track and queue of upcoming tracks
    """
    try:
        logger.info("🎵 Getting playback queue")
        result = spotify_client.queue()

        queue_tracks = []
        if result.get("queue"):
            queue_tracks = [parse_track(item) for item in result["queue"]]

        return QueueState(
            currently_playing=parse_track(result["currently_playing"])
            if result.get("currently_playing")
            else None,
            queue=queue_tracks,
        )
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Get Track Info",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def get_track_info(track_ids: str | list[str]) -> TrackList:
    """Get detailed information about one or more Spotify tracks.

    Args:
        track_ids: Single track ID or list of track IDs (up to 50)

    Returns:
        TrackList with 'tracks' containing track metadata including release_date.
        For single ID, returns {'tracks': [track]}.

    Note: Batch lookup is much more efficient - 50 tracks = 1 API call instead of 50.
    """
    try:
        # Normalize to list
        ids = [track_ids] if isinstance(track_ids, str) else track_ids

        if len(ids) > 50:
            raise ValueError("Maximum 50 track IDs per request (Spotify API limit)")

        logger.info(f"🎵 Getting track info for {len(ids)} track(s)")

        if len(ids) == 1:
            result = spotify_client.track(ids[0])
            tracks = [parse_track(result)]
        else:
            result = spotify_client.tracks(ids)
            tracks = [parse_track(item) for item in result.get("tracks", []) if item]

        return TrackList(tracks=tracks)
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Get Artist Info",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def get_artist_info(artist_id: str) -> ArtistInfo:
    """Get detailed information about a Spotify artist.

    Args:
        artist_id: Spotify artist ID
    Returns:
        ArtistInfo with the artist and their top tracks
    """
    try:
        logger.info(f"🎤 Getting artist info: {artist_id}")
        result: ArtistObject = spotify_client.artist(artist_id)
        top_tracks = spotify_client.artist_top_tracks(artist_id)

        followers = result.get("followers") or {}
        artist = Artist(
            name=result["name"],
            id=result["id"],
            genres=result.get("genres", []),
            popularity=result.get("popularity"),
            followers=followers.get("total"),
        )

        tracks = [parse_track(track) for track in top_tracks.get("tracks", [])[:10]]

        return ArtistInfo(artist=artist, top_tracks=tracks)
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Get Playlist Info",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def get_playlist_info(playlist_id: str) -> Playlist:
    """Get basic information about a Spotify playlist.

    Args:
        playlist_id: Spotify playlist ID

    Returns:
        Playlist metadata (no tracks - use get_playlist_tracks for tracks)

    Note: This returns playlist info only. For tracks, use get_playlist_tracks
    which supports full pagination for large playlists.
    """
    try:
        logger.info(f"📋 Getting playlist info: {playlist_id}")
        result: PlaylistObject = spotify_client.playlist(
            playlist_id, fields="id,name,description,owner,public,tracks.total"
        )

        owner = result.get("owner") or {}
        tracks = result.get("tracks") or {}
        playlist = Playlist(
            name=result["name"],
            id=result["id"],
            owner=owner.get("display_name"),
            description=result.get("description"),
            tracks=None,  # No tracks - use get_playlist_tracks
            total_tracks=tracks.get("total"),
            public=result.get("public"),
        )

        return playlist
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Create Playlist",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def create_playlist(name: str, description: str = "", public: bool = True) -> Playlist:
    """Create a new Spotify playlist.

    Args:
        name: Playlist name
        description: Playlist description (default: empty)
        public: Whether playlist is public (default: True)

    Returns:
        The created Playlist
    """
    try:
        logger.info(f"🎧 Creating playlist: '{name}' (public={public})")
        user = spotify_client.current_user()
        result = spotify_client.user_playlist_create(
            user["id"], name, public=public, description=description
        )

        playlist = Playlist(
            name=result["name"],
            id=result["id"],
            owner=result.get("owner", {}).get("display_name"),
            description=result.get("description"),
            tracks=[],
            total_tracks=0,
            public=result.get("public"),
        )

        return playlist

    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Add Tracks to Playlist",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def add_tracks_to_playlist(playlist_id: str, track_uris: list[str]) -> ActionResult:
    """Add tracks to a playlist.

    Args:
        playlist_id: Playlist ID
        track_uris: List of track URIs (up to 100)
    """
    try:
        # Convert track IDs to URIs if needed
        uris = [
            uri if uri.startswith("spotify:track:") else f"spotify:track:{uri}"
            for uri in track_uris
        ]

        logger.info(f"🎧 Adding {len(uris)} tracks to playlist {playlist_id}")
        result = spotify_client.playlist_add_items(playlist_id, uris)
        return ActionResult(
            status="success",
            message=f"Added {len(uris)} tracks to playlist",
            snapshot_id=result.get("snapshot_id") if result else None,
        )

    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="List My Playlists",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def get_user_playlists(limit: int = 20, offset: int = 0) -> PlaylistList:
    """Get current user's playlists with pagination support.

    Args:
        limit: Max playlists to return per page (1-50, default 20)
        offset: Number of playlists to skip for pagination (default 0)

    Returns:
        PlaylistList with 'items' (list of playlists) and pagination info ('total', 'limit', 'offset')

    Note: For users with many playlists, use offset to paginate through results.
    Example: offset=0 gets playlists 1-20, offset=20 gets playlists 21-40, etc.
    """
    try:
        # Validate limit (Spotify API accepts 1-50)
        limit = max(1, min(50, limit))

        logger.info(f"📋 Getting user playlists (limit={limit}, offset={offset})")
        result = spotify_client.current_user_playlists(limit=limit, offset=offset)

        # Log pagination info
        log_pagination_info("get_user_playlists", result.get("total", 0), limit, offset)

        playlists = []
        for item in result.get("items", []):
            item_typed = cast("PlaylistObject", item)
            owner = item_typed.get("owner") or {}
            tracks = item_typed.get("tracks") or {}
            playlist = Playlist(
                name=item_typed["name"],
                id=item_typed["id"],
                owner=owner.get("display_name"),
                description=item_typed.get("description"),
                total_tracks=tracks.get("total"),
                public=item_typed.get("public"),
            )
            playlists.append(playlist)

        return PlaylistList(
            items=playlists,
            total=result.get("total", 0),
            limit=result.get("limit", limit),
            offset=result.get("offset", offset),
            next=result.get("next"),
            previous=result.get("previous"),
        )

    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Get Playlist Tracks",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
async def get_playlist_tracks(
    playlist_id: str,
    limit: int | None = None,
    offset: int = 0,
    ctx: Context | None = None,
) -> PlaylistTracks:
    """Get tracks from a playlist with full pagination support.

    Args:
        playlist_id: Playlist ID
        limit: Max tracks to return (None for all tracks, up to 10,000 safety limit)
        offset: Number of tracks to skip for pagination (default 0)

    Returns:
        PlaylistTracks with 'items' (list of tracks), 'total', 'limit', 'offset'

    Note: Large playlists require pagination. Use limit/offset to get specific ranges:
    - Get first 100: limit=100, offset=0
    - Get next 100: limit=100, offset=100
    - Get all tracks: limit=None (use with caution on very large playlists)
    """
    try:
        logger.info(
            f"📋 Getting playlist tracks: {playlist_id} (limit={limit}, offset={offset})"
        )

        # Fetch total up front so progress notifications have a denominator
        playlist_info = spotify_client.playlist(playlist_id, fields="tracks.total")
        total_tracks = (playlist_info.get("tracks") or {}).get("total")

        tracks = await get_playlist_tracks_paginated(
            playlist_id, limit, offset, ctx=ctx, total=total_tracks
        )
        if total_tracks is None:
            total_tracks = len(tracks)

        # Log pagination info
        log_pagination_info("get_playlist_tracks", total_tracks, limit, offset)
        logger.info(f"📋 Retrieved {len(tracks)} tracks from playlist {playlist_id}")

        return PlaylistTracks(
            items=tracks,
            total=total_tracks,
            limit=limit,
            offset=offset,
            returned=len(tracks),
        )

    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Remove Tracks from Playlist",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
async def remove_tracks_from_playlist(
    playlist_id: str, track_uris: list[str], ctx: Context | None = None
) -> ActionResult:
    """Remove tracks from a playlist.

    Args:
        playlist_id: Playlist ID
        track_uris: List of track URIs to remove
    """
    try:
        # Convert track IDs to URIs if needed
        uris = [
            uri if uri.startswith("spotify:track:") else f"spotify:track:{uri}"
            for uri in track_uris
        ]

        # Confirm this destructive op, but only when the client actually
        # advertises elicitation support. If it doesn't, proceed so this core
        # op never hard-fails on capability-poor clients. If it does support
        # elicitation and the prompt errors, we let that propagate rather than
        # silently deleting — an unconfirmed destructive edit must not slip through.
        if ctx is not None and ctx.session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability())
        ):
            response = await ctx.elicit(
                message=(
                    f"Remove {len(uris)} track(s) from playlist {playlist_id}? "
                    "This cannot be undone."
                ),
                schema=RemovalConfirmation,
            )
            confirmed = response.action == "accept" and bool(
                response.data and response.data.confirm
            )
            if not confirmed:
                return ActionResult(
                    status="cancelled",
                    message="Removal cancelled by user",
                )

        logger.info(f"🚮 Removing {len(uris)} tracks from playlist {playlist_id}")
        result = spotify_client.playlist_remove_all_occurrences_of_items(
            playlist_id, uris
        )
        return ActionResult(
            status="success",
            message=f"Removed {len(uris)} tracks from playlist",
            snapshot_id=result.get("snapshot_id") if result else None,
        )

    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Edit Playlist Details",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def modify_playlist_details(
    playlist_id: str,
    name: str | None = None,
    description: str | None = None,
    public: bool | None = None,
) -> ActionResult:
    """Modify playlist details.

    Args:
        playlist_id: Playlist ID
        name: New playlist name (optional)
        description: New playlist description (optional)
        public: Whether playlist should be public (optional)
    """
    try:
        if not name and not description and public is None:
            raise ValueError(
                "At least one of name, description, or public must be provided"
            )

        updates = []
        if name:
            updates.append(f"name='{name}'")
        if description:
            updates.append(f"description='{description}'")
        if public is not None:
            updates.append(f"public={public}")
        logger.info(f"📋 Modifying playlist {playlist_id}: {', '.join(updates)}")

        spotify_client.playlist_change_details(
            playlist_id, name=name, description=description, public=public
        )
        return ActionResult(
            status="success", message="Playlist details updated successfully"
        )

    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Reorder Playlist Tracks",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def reorder_playlist_tracks(
    playlist_id: str,
    range_start: int,
    insert_before: int,
    range_length: int = 1,
    snapshot_id: str | None = None,
) -> ActionResult:
    """Move a contiguous block of tracks to a new position within a playlist.

    Args:
        playlist_id: Playlist ID
        range_start: Zero-based position of the first track to move
        insert_before: Zero-based position to insert the moved block before.
            Pass the playlist's total track count to move the block to the end.
        range_length: Number of consecutive tracks to move (default 1)
        snapshot_id: Optional playlist snapshot ID to guard against concurrent edits

    Returns:
        ActionResult with the new snapshot_id

    Note: Positions are zero-based. Example: move the first 3 tracks to just
    before position 10 with range_start=0, range_length=3, insert_before=10.
    """
    try:
        if range_start < 0 or insert_before < 0:
            raise ValueError("range_start and insert_before must be >= 0")
        if range_length < 1:
            raise ValueError("range_length must be >= 1")

        logger.info(
            f"🔀 Reordering playlist {playlist_id}: move {range_length} track(s) "
            f"from {range_start} before {insert_before}"
        )
        result = spotify_client.playlist_reorder_items(
            playlist_id,
            range_start=range_start,
            insert_before=insert_before,
            range_length=range_length,
            snapshot_id=snapshot_id,
        )
        return ActionResult(
            status="success",
            message=(
                f"Moved {range_length} track(s) from position {range_start} "
                f"to before position {insert_before}"
            ),
            snapshot_id=result.get("snapshot_id") if result else None,
        )
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Get Album Info",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def get_album_info(album_id: str) -> AlbumInfo:
    """Get detailed information about a Spotify album.

    Args:
        album_id: Spotify album ID

    Returns:
        AlbumInfo with album metadata (release_date, label) and its tracks
    """
    try:
        logger.info(f"💿 Getting album info: {album_id}")
        result: AlbumObject = spotify_client.album(album_id)

        result_artists = result.get("artists", [])
        album = Album(
            name=result["name"],
            id=result["id"],
            artist=result_artists[0]["name"] if result_artists else "Unknown",
            artists=[a["name"] for a in result_artists],
            release_date=result.get("release_date"),
            release_date_precision=result.get("release_date_precision"),
            total_tracks=result.get("total_tracks"),
            album_type=result.get("album_type"),
            label=result.get("label"),
            genres=result.get("genres", []),
            popularity=result.get("popularity"),
            external_urls=cast("dict[str, str]", result.get("external_urls")),
        )

        # Parse album tracks
        tracks = []
        album_tracks = result.get("tracks") or {}
        for item in album_tracks.get("items", []):
            if item:
                # Album track items don't have album info, add it
                item["album"] = cast(
                    "AlbumRef",
                    {
                        "name": result["name"],
                        "id": result["id"],
                        "release_date": result.get("release_date"),
                    },
                )
                tracks.append(parse_track(item))

        return AlbumInfo(album=album, tracks=tracks)
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


@mcp.tool(
    title="Get Liked Songs",
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True
    ),
    icons=[SPOTIFY_ICON],
)
@log_tool_execution
def get_saved_tracks(limit: int = 20, offset: int = 0) -> SavedTracks:
    """Get user's saved/liked tracks (Liked Songs library).

    Args:
        limit: Max tracks to return per page (1-50, default 20)
        offset: Number of tracks to skip for pagination (default 0)

    Returns:
        SavedTracks with 'items' (tracks with added_at timestamp) and pagination info
    """
    try:
        limit = max(1, min(50, limit))

        logger.info(f"❤️ Getting saved tracks (limit={limit}, offset={offset})")
        result = spotify_client.current_user_saved_tracks(limit=limit, offset=offset)

        tracks = []
        for item in result.get("items", []):
            if item and item.get("track"):
                track = parse_track(item["track"])
                track.added_at = item.get("added_at")
                tracks.append(track)

        log_pagination_info("get_saved_tracks", result.get("total", 0), limit, offset)

        return SavedTracks(
            items=tracks,
            total=result.get("total", 0),
            limit=result.get("limit", limit),
            offset=result.get("offset", offset),
            next=result.get("next"),
            previous=result.get("previous"),
        )
    except SpotifyException as e:
        raise convert_spotify_error(e) from e


# === RESOURCES ===


@mcp.resource("spotify://user/current", icons=[SPOTIFY_ICON])
def current_user() -> str:
    """Current user's profile."""
    try:
        user = spotify_client.current_user()
        return json.dumps(
            {
                "id": user.get("id"),
                "display_name": user.get("display_name"),
                "followers": user.get("followers", {}).get("total"),
                "country": user.get("country"),
                "product": user.get("product"),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("spotify://playback/current", icons=[SPOTIFY_ICON])
def current_playback_resource() -> str:
    """Current playback state."""
    try:
        playback = spotify_client.current_playback()
        if not playback:
            return json.dumps({"status": "no_playback"})

        track_info = playback.get("item", {})
        return json.dumps(
            {
                "is_playing": playback.get("is_playing", False),
                "track": {
                    "name": track_info.get("name"),
                    "artist": track_info.get("artists", [{}])[0].get("name"),
                    "album": track_info.get("album", {}).get("name"),
                    "id": track_info.get("id"),
                }
                if track_info
                else None,
                "device": playback.get("device", {}).get("name"),
                "progress_ms": playback.get("progress_ms"),
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("spotify://track/{track_id}", icons=[SPOTIFY_ICON])
def track_resource(track_id: str) -> str:
    """A single track by ID."""
    try:
        return parse_track(spotify_client.track(track_id)).model_dump_json()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("spotify://playlist/{playlist_id}", icons=[SPOTIFY_ICON])
def playlist_resource(playlist_id: str) -> str:
    """A playlist's metadata by ID (no tracks)."""
    try:
        result = spotify_client.playlist(
            playlist_id, fields="id,name,description,owner,public,tracks.total"
        )
        owner = result.get("owner") or {}
        tracks = result.get("tracks") or {}
        return Playlist(
            name=result["name"],
            id=result["id"],
            owner=owner.get("display_name"),
            description=result.get("description"),
            total_tracks=tracks.get("total"),
            public=result.get("public"),
        ).model_dump_json()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("spotify://artist/{artist_id}", icons=[SPOTIFY_ICON])
def artist_resource(artist_id: str) -> str:
    """An artist by ID."""
    try:
        result = spotify_client.artist(artist_id)
        followers = result.get("followers") or {}
        return Artist(
            name=result["name"],
            id=result["id"],
            genres=result.get("genres", []),
            popularity=result.get("popularity"),
            followers=followers.get("total"),
        ).model_dump_json()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("spotify://album/{album_id}", icons=[SPOTIFY_ICON])
def album_resource(album_id: str) -> str:
    """An album's metadata by ID (no tracks)."""
    try:
        result = spotify_client.album(album_id)
        artists = result.get("artists", [])
        return Album(
            name=result["name"],
            id=result["id"],
            artist=artists[0]["name"] if artists else "Unknown",
            artists=[a["name"] for a in artists],
            release_date=result.get("release_date"),
            release_date_precision=result.get("release_date_precision"),
            total_tracks=result.get("total_tracks"),
            album_type=result.get("album_type"),
            label=result.get("label"),
            genres=result.get("genres", []),
            popularity=result.get("popularity"),
            external_urls=cast("dict[str, str]", result.get("external_urls")),
        ).model_dump_json()
    except Exception as e:
        return json.dumps({"error": str(e)})


# === PROMPTS ===


@mcp.prompt(icons=[SPOTIFY_ICON])
def create_mood_playlist(mood: str, genre: str = "", decade: str = "") -> str:
    """Create a playlist based on mood and preferences."""
    prompt = f"Create a Spotify playlist for a {mood} mood"

    if genre:
        prompt += f" with {genre} music"
    if decade:
        prompt += f" from the {decade}"

    return f"""{prompt}.

Workflow:
1. Use search_tracks with different queries to find diverse songs
   - For large search results, use offset parameter to get more options
   - Example: search_tracks("upbeat pop", limit=20, offset=0) then offset=20 for more
2. Create playlist with create_playlist
3. Add tracks with add_tracks_to_playlist (supports up to 100 tracks per call)

Pagination Tips:
- Search results are paginated (limit=1-50, use offset for more results)
- For variety, try multiple search queries with different offsets
- Large playlists: batch add tracks in groups of 50-100

Consider:
1. Energy level for {mood} mood
2. {f"Focus on {genre}" if genre else "Genre variety"}
3. {f"Songs from {decade}" if decade else "Mix of eras"}
4. 15-20 songs with good flow"""


@mcp.prompt(icons=[SPOTIFY_ICON])
def analyze_large_playlist(playlist_id: str, analysis_type: str = "overview") -> str:
    """Analyze a large playlist efficiently using pagination."""
    return f"""Analyze playlist {playlist_id} with focus on {analysis_type}.

For large playlists (>100 tracks), use pagination to analyze efficiently:

Step 1: Get overview
- Use get_item_info(playlist_id, "playlist") for basic info and first 50 tracks
- Check total_tracks to understand playlist size

Step 2: Full analysis (if needed)
- For playlists >100 tracks, use get_playlist_tracks with pagination:
  - get_playlist_tracks(playlist_id, limit=100, offset=0) for first 100
  - get_playlist_tracks(playlist_id, limit=100, offset=100) for next 100
  - Continue until you have all tracks or sufficient sample

Step 3: Analysis
Based on analysis_type:
- "overview": Basic stats, genres, mood distribution
- "detailed": Track-by-track analysis, recommendations
- "duplicates": Find duplicate tracks across large playlist
- "mood": Analyze mood/energy progression through playlist

Pagination Benefits:
- Memory efficient for 1000+ track playlists
- Can stop early if sufficient data collected
- Allows progressive analysis with user feedback"""


@mcp.prompt(icons=[SPOTIFY_ICON])
def discover_music_systematically(
    seed_query: str, exploration_depth: str = "medium"
) -> str:
    """Systematically discover music using search pagination."""
    return f"""Discover music related to "{seed_query}" with {exploration_depth} exploration.

Search Strategy with Pagination:
1. Initial search: search_tracks("{seed_query}", limit=20, offset=0)
2. Diverse results: Use different offsets to explore deeper:
   - Popular results: offset=0-20
   - Hidden gems: offset=20-40, offset=40-60
   - Deep cuts: offset=80-100+

3. Related searches with pagination:
   - Artist names from initial results
   - Album names from initial results
   - Genre + decade combinations
   - Similar mood/energy descriptors

Exploration Depth:
- "light": 2-3 search queries, 20 results each
- "medium": 5-6 search queries, explore offsets 0-40
- "deep": 10+ search queries, explore offsets 0-100+

Pagination Best Practices:
- Start with limit=20 for quick overview
- Use offset to avoid duplicate results
- Try different query variations rather than just advancing offset
- Stop when you find enough quality matches

Output: Curated list of 15-25 discovered tracks with variety"""


# === HTTP TRANSPORT ===


def _bearer_guard(app: ASGIApp, token: str) -> ASGIApp:
    """Wrap an ASGI app so HTTP requests must carry `Authorization: Bearer <token>`.

    Non-http scopes (e.g. lifespan) pass straight through so the streamable-HTTP
    session manager still starts. Uses a constant-time compare.
    """
    expected = b"Bearer " + token.encode()

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"")
            if not hmac.compare_digest(provided, expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await app(scope, receive, send)

    return guarded


def build_http_app(bearer_token: str | None = None) -> ASGIApp:
    """Build the streamable-HTTP ASGI app, optionally gated by a bearer token.

    Used by the CLI http path and by remote deploy entrypoints (e.g. Modal).
    """
    app: ASGIApp = mcp.streamable_http_app()
    if bearer_token:
        app = _bearer_guard(app, bearer_token)
    return app


if __name__ == "__main__":
    mcp.run()
