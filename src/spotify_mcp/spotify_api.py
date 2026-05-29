from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from .utils import normalize_redirect_uri

if TYPE_CHECKING:
    from spotipy.cache_handler import CacheFileHandler


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
        except Exception:  # nosec B110 - intentional fallback for optional config file
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
    """Owns Spotify OAuth setup and exposes the authenticated spotipy client.

    The MCP tools in `fastmcp_server` talk to `self.sp` (the raw spotipy
    client) directly; this wrapper only handles auth/token management.
    """

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
