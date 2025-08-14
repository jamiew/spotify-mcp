import logging
import os
import tomllib
from pathlib import Path
from typing import Any

import spotipy
from dotenv import load_dotenv
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

from . import utils
from .utils import normalize_redirect_uri


def load_config() -> dict[str, str | None]:
    """Load configuration with precedence: env vars > .env file > pyproject.toml defaults."""
    # First try environment variables and .env file
    load_dotenv()

    config = {
        "CLIENT_ID": os.getenv("SPOTIFY_CLIENT_ID"),
        "CLIENT_SECRET": os.getenv("SPOTIFY_CLIENT_SECRET"),
        "REDIRECT_URI": os.getenv("SPOTIFY_REDIRECT_URI"),
    }

    # If any values are missing, load defaults from pyproject.toml
    if not all(config.values()):
        try:
            pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
            if pyproject_path.exists():
                with open(pyproject_path, "rb") as f:
                    pyproject_data = tomllib.load(f)

                defaults = (
                    pyproject_data.get("tool", {}).get("spotify-mcp", {}).get("env", {})
                )
                config["CLIENT_ID"] = config["CLIENT_ID"] or defaults.get(
                    "SPOTIFY_CLIENT_ID"
                )
                config["CLIENT_SECRET"] = config["CLIENT_SECRET"] or defaults.get(
                    "SPOTIFY_CLIENT_SECRET"
                )
                config["REDIRECT_URI"] = config["REDIRECT_URI"] or defaults.get(
                    "SPOTIFY_REDIRECT_URI"
                )
        except Exception:
            # Fallback to None if pyproject.toml reading fails
            pass

    return config


# Load configuration using the hierarchical approach
config = load_config()
CLIENT_ID = config["CLIENT_ID"]
CLIENT_SECRET = config["CLIENT_SECRET"]
REDIRECT_URI = (
    normalize_redirect_uri(config["REDIRECT_URI"]) if config["REDIRECT_URI"] else None
)

# Define all required scopes
SCOPES = [
    # Playback
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-modify-playback-state",
    "app-remote-control",
    "streaming",
    # Playlists
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    # Library
    "user-library-read",
    "user-library-modify",
    # History
    "user-read-playback-position",
    "user-top-read",
    "user-read-recently-played",
]


class Client:
    sp: spotipy.Spotify
    auth_manager: SpotifyOAuth
    cache_handler: CacheFileHandler
    logger: logging.Logger

    def __init__(self, logger: logging.Logger | None = None):
        """Initialize Spotify client with necessary permissions"""
        self.logger = logger or logging.getLogger(__name__)

        # Use all defined scopes
        scope = ",".join(SCOPES)
        self.logger.info(f"Initializing Spotify client with scopes: {scope}")

        try:
            auth_manager = SpotifyOAuth(
                scope=scope,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
            )

            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            self.auth_manager = auth_manager
            self.cache_handler = auth_manager.cache_handler
            self.logger.info("Successfully initialized Spotify client")
        except Exception as e:
            self.logger.error(
                f"Failed to initialize Spotify client: {str(e)}", exc_info=True
            )
            raise

    def search(
        self, query: str, qtype: str = "track", limit: int = 10
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Searches based of query term.
        - query: query term
        - qtype: the types of items to return. One or more of 'artist', 'album',  'track', 'playlist'.
                 If multiple types are desired, pass in a comma separated string; e.g. 'track,album'
        - limit: max # items to return
        """
        results = self.sp.search(q=query, limit=limit, type=qtype)
        search_results = utils.parse_search_results(results, qtype)
        return search_results if search_results else {}

    def recommendations(
        self,
        artists: list[str] | None = None,
        tracks: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        recs = self.sp.recommendations(
            seed_artists=artists, seed_tracks=tracks, limit=limit
        )
        return recs if recs else {}

    def get_info(self, item_id: str, qtype: str = "track") -> dict[str, Any]:
        """
        Returns more info about item.
        - item_id: id.
        - qtype: Either 'track', 'album', 'artist', or 'playlist'.
        """
        match qtype:
            case "track":
                track_info = utils.parse_track(self.sp.track(item_id), detailed=True)
                return track_info if track_info else {}
            case "album":
                album_info = utils.parse_album(self.sp.album(item_id), detailed=True)
                return album_info if album_info else {}
            case "artist":
                artist_info = utils.parse_artist(self.sp.artist(item_id), detailed=True)
                if not artist_info:
                    return {}
                albums = self.sp.artist_albums(item_id)
                top_tracks_response = self.sp.artist_top_tracks(item_id)
                if not top_tracks_response:
                    return artist_info

                albums_and_tracks = {
                    "albums": albums,
                    "tracks": {"items": top_tracks_response.get("tracks", [])},
                }
                parsed_info = utils.parse_search_results(
                    albums_and_tracks, qtype="album,track"
                )
                artist_info["top_tracks"] = parsed_info.get("tracks", [])
                artist_info["albums"] = parsed_info.get("albums", [])
                return artist_info
            case "playlist":
                playlist = self.sp.playlist(item_id)
                playlist_info = utils.parse_playlist(playlist, detailed=True)
                return playlist_info if playlist_info else {}

        raise ValueError(f"unknown qtype {qtype}")

    def get_current_track(self) -> dict[str, Any] | None:
        """Get information about the currently playing track"""
        try:
            current = self.sp.current_user_playing_track()
            if not current:
                self.logger.info("No playback session found")
                return None
            if current.get("currently_playing_type") != "track":
                self.logger.info("Current playback is not a track")
                return None

            track_info = utils.parse_track(current.get("item"))
            if not track_info:
                return None

            if "is_playing" in current:
                track_info["is_playing"] = current["is_playing"]

            self.logger.info(
                f"Current track: {track_info.get('name', 'Unknown')} by {track_info.get('artist', 'Unknown')}"
            )
            return track_info
        except Exception:
            self.logger.error("Error getting current track info", exc_info=True)
            raise

    @utils.validate
    def start_playback(
        self, track_id: str | None = None, device: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """
        Starts track playback. If track_id is omitted, resumes current playback.
        - track_id: ID of track to play, or None.
        """
        try:
            if not track_id:
                if self.is_track_playing():
                    self.logger.info(
                        "No track_id provided and playback already active."
                    )
                    return None
                if not self.get_current_track():
                    raise ValueError(
                        "No track_id provided and no current playback to resume."
                    )

            uris = [f"spotify:track:{track_id}"] if track_id else None
            device_id = device.get("id") if device else None

            result = self.sp.start_playback(uris=uris, device_id=device_id)
            self.logger.info(
                f"Playback started successfully{' for track_id: ' + track_id if track_id else ''}"
            )
            return result or {}
        except Exception as e:
            self.logger.error(f"Error starting playback: {str(e)}", exc_info=True)
            raise

    @utils.validate
    def pause_playback(self, device: dict[str, Any] | None = None) -> None:
        """Pauses playback."""
        playback = self.sp.current_playback()
        if playback and playback.get("is_playing"):
            self.sp.pause_playback(device.get("id") if device else None)

    @utils.validate
    def add_to_queue(self, track_id: str, device: dict[str, Any] | None = None) -> None:
        """
        Adds track to queue.
        - track_id: ID of track to play.
        """
        self.sp.add_to_queue(track_id, device.get("id") if device else None)

    @utils.validate
    def get_queue(self, device: dict[str, Any] | None = None) -> dict[str, Any]:
        """Returns the current queue of tracks."""
        queue_info = self.sp.queue()
        if not queue_info:
            return {"currently_playing": None, "queue": []}

        self.logger.info(
            f"currently playing keys {queue_info.get('currently_playing', {}).keys()}"
        )

        queue_info["currently_playing"] = self.get_current_track()
        queue = queue_info.pop("queue", [])
        queue_info["queue"] = [
            track_info
            for track in queue
            if (track_info := utils.parse_track(track)) is not None
        ]

        return queue_info or {}

    def get_liked_songs(self) -> list[dict[str, Any]]:
        results = self.sp.current_user_saved_tracks()
        if not results or "items" not in results:
            return []

        tracks = []
        for item in results["items"]:
            if track := item.get("track"):
                if track_info := utils.parse_track(track):
                    tracks.append(track_info)
        return tracks

    def is_track_playing(self) -> bool:
        """Returns if a track is actively playing."""
        curr_track = self.get_current_track()
        if not curr_track:
            return False
        return bool(curr_track.get("is_playing"))

    def get_devices(self) -> list[dict[str, Any]]:
        """Get list of available devices"""
        devices = self.sp.devices()
        return devices.get("devices", []) if devices else []

    def is_active_device(self) -> bool:
        """Check if there is an active device"""
        return any(device.get("is_active", False) for device in self.get_devices())

    def _get_candidate_device(self) -> dict[str, Any] | None:
        """Get an active device or the first available device"""
        devices = self.get_devices()
        if not devices:
            return None

        for device in devices:
            if device.get("is_active"):
                return device
        self.logger.info(
            f"No active device, assigning {devices[0].get('name', 'Unknown')}."
        )
        return devices[0]

    def auth_ok(self) -> bool:
        """Check if authentication is valid"""
        try:
            result = self.auth_manager.is_token_expired(
                self.cache_handler.get_cached_token()
            )
            self.logger.info(
                f"Auth check result: {'valid' if not result else 'expired'}"
            )
            return not result
        except Exception as e:
            self.logger.error(f"Error checking auth status: {str(e)}", exc_info=True)
            raise

    def auth_refresh(self) -> None:
        """Refresh authentication token"""
        self.auth_manager.validate_token(self.cache_handler.get_cached_token())

    def skip_track(self, n: int = 1) -> None:
        """Skip n tracks"""
        for _ in range(n):
            self.sp.next_track()

    def previous_track(self) -> None:
        """Go to previous track"""
        self.sp.previous_track()

    def seek_to_position(self, position_ms: int) -> None:
        """Seek to position in current track"""
        self.sp.seek_track(position_ms=position_ms)

    def set_volume(self, volume_percent: int) -> None:
        """Set playback volume"""
        self.sp.volume(volume_percent)

    # Playlist Methods
    def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        """Get a playlist's details"""
        try:
            self.logger.info(f"Getting playlist with ID: {playlist_id}")
            playlist = self.sp.playlist(playlist_id)
            playlist_info = utils.parse_playlist(playlist, detailed=True)
            if playlist_info:
                self.logger.info(
                    f"Successfully retrieved playlist: {playlist_info.get('name', 'Unknown')}"
                )
            else:
                self.logger.warning(
                    f"Retrieved empty playlist info for ID: {playlist_id}"
                )
            return playlist_info if playlist_info else {}
        except Exception as e:
            self.logger.error(f"Error getting playlist: {str(e)}", exc_info=True)
            raise

    def update_playlist_details(
        self,
        playlist_id: str,
        name: str | None = None,
        description: str | None = None,
        public: bool | None = None,
    ) -> None:
        """Update a playlist's details"""
        try:
            self.logger.info(
                f"Updating playlist {playlist_id} with name: {name}, description: {description}, public: {public}"
            )
            self.sp.playlist_change_details(
                playlist_id, name=name, description=description, public=public
            )
            self.logger.info(
                f"Successfully updated playlist details for ID: {playlist_id}"
            )
        except Exception as e:
            self.logger.error(f"Error updating playlist: {str(e)}", exc_info=True)
            raise

    def update_playlist_items(
        self,
        playlist_id: str,
        uris: list[str],
        range_start: int | None = None,
        insert_before: int | None = None,
        range_length: int | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, str]:
        """Update a playlist's items"""
        try:
            self.logger.info(
                f"Updating playlist {playlist_id} items. URIs count: {len(uris)}"
            )
            self.logger.info(
                f"Range params - start: {range_start}, insert_before: {insert_before}, length: {range_length}"
            )

            result = self.sp.playlist_replace_items(playlist_id, uris)
            if range_start is not None and insert_before is not None:
                self.logger.info(f"Reordering items in playlist {playlist_id}")
                self.sp.playlist_reorder_items(
                    playlist_id,
                    range_start=range_start,
                    insert_before=insert_before,
                    range_length=range_length or 1,
                    snapshot_id=snapshot_id,
                )

            snapshot_id = (
                result["snapshot_id"] if result and isinstance(result, dict) else ""
            )
            self.logger.info(
                f"Successfully updated playlist items. Snapshot ID: {snapshot_id}"
            )
            return {"snapshot_id": snapshot_id}
        except Exception as e:
            self.logger.error(f"Error updating playlist items: {str(e)}", exc_info=True)
            raise

    def add_playlist_items(
        self, playlist_id: str, uris: list[str], position: int | None = None
    ) -> dict[str, str]:
        """Add items to a playlist"""
        try:
            self.logger.info(
                f"Adding {len(uris)} items to playlist {playlist_id} at position: {position}"
            )
            result = self.sp.playlist_add_items(playlist_id, uris, position=position)
            snapshot_id = (
                result["snapshot_id"] if result and isinstance(result, dict) else ""
            )
            self.logger.info(
                f"Successfully added items to playlist. Snapshot ID: {snapshot_id}"
            )
            return {"snapshot_id": snapshot_id}
        except Exception as e:
            self.logger.error(
                f"Error adding items to playlist: {str(e)}", exc_info=True
            )
            raise

    def remove_playlist_items(
        self, playlist_id: str, uris: list[str], snapshot_id: str | None = None
    ) -> dict[str, str]:
        """Remove items from a playlist"""
        try:
            self.logger.info(f"Removing {len(uris)} items from playlist {playlist_id}")
            result = self.sp.playlist_remove_all_occurrences_of_items(
                playlist_id, uris, snapshot_id=snapshot_id
            )
            snapshot_id = (
                result["snapshot_id"] if result and isinstance(result, dict) else ""
            )
            self.logger.info(
                f"Successfully removed items from playlist. Snapshot ID: {snapshot_id}"
            )
            return {"snapshot_id": snapshot_id}
        except Exception as e:
            self.logger.error(
                f"Error removing items from playlist: {str(e)}", exc_info=True
            )
            raise

    def get_user_playlists(
        self, user_id: str | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """Get a user's playlists"""
        try:
            self.logger.info(
                f"Getting playlists for user: {user_id if user_id else 'current user'}"
            )
            self.logger.info(f"Limit: {limit}, Offset: {offset}")

            if user_id:
                playlists = self.sp.user_playlists(user_id, limit=limit, offset=offset)
            else:
                playlists = self.sp.current_user_playlists(limit=limit, offset=offset)

            if not playlists:
                self.logger.info("No playlists found")
                return {
                    "items": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "next": None,
                    "previous": None,
                }

            # Parse the playlists
            result = {
                "items": [
                    playlist_info
                    for playlist in playlists.get("items", [])
                    if (playlist_info := utils.parse_playlist(playlist)) is not None
                ],
                "total": playlists.get("total", 0),
                "limit": playlists.get("limit", limit),
                "offset": playlists.get("offset", offset),
                "next": playlists.get("next"),
                "previous": playlists.get("previous"),
            }

            self.logger.info(f"Successfully retrieved {len(result['items'])} playlists")
            return result
        except Exception as e:
            self.logger.error(f"Error getting user playlists: {str(e)}", exc_info=True)
            raise

    def create_playlist(
        self, user_id: str, name: str, description: str = "", public: bool = False
    ) -> dict[str, Any]:
        """Create a new playlist"""
        try:
            self.logger.info(f"Creating playlist '{name}' for user {user_id}")
            self.logger.info(f"Description: {description}, Public: {public}")

            playlist = self.sp.user_playlist_create(
                user_id, name, public=public, description=description
            )
            playlist_info = utils.parse_playlist(playlist, detailed=True)

            if playlist_info:
                self.logger.info(
                    f"Successfully created playlist. ID: {playlist_info.get('id', 'Unknown')}"
                )
            else:
                self.logger.warning("Created playlist but received empty playlist info")

            return playlist_info if playlist_info else {}
        except Exception as e:
            self.logger.error(f"Error creating playlist: {str(e)}", exc_info=True)
            raise

    def get_playlist_cover_image(self, playlist_id: str) -> list[dict[str, Any]]:
        """Get a playlist's cover image"""
        try:
            self.logger.info(f"Getting cover image for playlist: {playlist_id}")
            images = self.sp.playlist_cover_image(playlist_id)

            if images:
                self.logger.info(f"Successfully retrieved {len(images)} cover images")
            else:
                self.logger.info("No cover images found")

            return images if images else []
        except Exception as e:
            self.logger.error(f"Error getting playlist cover: {str(e)}", exc_info=True)
            raise

    def upload_playlist_cover_image(self, playlist_id: str, image_data: str) -> None:
        """Upload a custom playlist cover image"""
        try:
            self.logger.info(f"Uploading cover image for playlist: {playlist_id}")
            self.logger.info(f"Image data length: {len(image_data)} characters")

            self.sp.playlist_upload_cover_image(playlist_id, image_data)
            self.logger.info("Successfully uploaded playlist cover image")
        except Exception as e:
            self.logger.error(
                f"Error uploading playlist cover: {str(e)}", exc_info=True
            )
            raise
