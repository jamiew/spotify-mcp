from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import spotipy
from dotenv import load_dotenv
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .utils import normalize_redirect_uri, to_id, to_uri

if TYPE_CHECKING:
    from collections.abc import Callable

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
    # Profile
    "user-read-private",
    "user-read-email",
]

# Retry transient server errors but never 429. Since July 2026 Spotify counts quota
# per developer account, so retrying a QUOTA_EXCEEDED 429 burns the pool for every
# app you own; surface it instead and let the caller back off.
RETRY_STATUS_CODES = (500, 502, 503, 504)


class Client:
    """Owns Spotify OAuth setup and exposes the authenticated spotipy client.

    The MCP tools in `fastmcp_server` talk to `self.sp` (the raw spotipy client)
    directly, except for the operations whose path differs between the two 2026
    API regimes — those go through the `with_fallback` helpers below.
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

            self.sp = spotipy.Spotify(
                auth_manager=auth_manager, status_forcelist=RETRY_STATUS_CODES
            )
            self.auth_manager = auth_manager
            self.cache_handler = auth_manager.cache_handler
            self.logger.info("Successfully initialized Spotify client")
        except Exception as e:
            self.logger.error(
                f"Failed to initialize Spotify client: {str(e)}", exc_info=True
            )
            raise


# === Feb 2026 regime fallback ===
#
# Spotify's February 2026 changes split apps into a "restricted" and a
# "full/legacy" regime that serve different paths for the same operation. Which
# one an app gets is not introspectable, so we try the restricted shape, fall
# back to the legacy one, and remember the answer for the life of the process.
# Paths and request bodies below are ported from the live-verified implementation
# in https://github.com/jamiew/spotify-mcp-cloudflare (src/endpoints.ts).

# Endpoint families confirmed to need the legacy path.
_legacy_families: set[str] = set()

# Statuses that mean "this route isn't served for us" rather than "not found".
# 400 is included because the restricted /me/library routes reject rather than
# 404 when the app is actually on the legacy regime.
_REGIME_MISS_STATUSES = frozenset({400, 404, 405, 410})


def with_fallback[T](
    family: str, restricted: Callable[[], T], legacy: Callable[[], T]
) -> T:
    """Try the restricted request shape for `family`, falling back to the legacy one."""
    if family in _legacy_families:
        return legacy()
    try:
        return restricted()
    except SpotifyException as e:
        if e.http_status not in _REGIME_MISS_STATUSES:
            raise
        # Only cache the family once the legacy shape actually works — a genuine
        # not-found fails both ways and must not pin us to the wrong regime.
        result = legacy()
        _legacy_families.add(family)
        logging.getLogger(__name__).info(
            f"Spotify '{family}' endpoints resolved to the legacy regime"
        )
        return result


# Spotify caps every batch read, and a restricted app can have the batch route
# withheld entirely (403) while the single-item route keeps working — /v1/tracks?ids=
# is the known case. This is a different shape from `with_fallback`: there is no
# alternative path to swap in, only a different number of requests. Remember a
# withheld kind so the 403 is paid once per process instead of on every call.
BATCH_LIMITS: dict[str, int] = {"track": 50, "artist": 50, "album": 20}
_batch_withheld: set[str] = set()


def _batch_read(
    kind: str,
    ids: list[str],
    *,
    batch: Callable[[list[str]], dict],
    single: Callable[[str], dict],
) -> list[dict]:
    """Read several objects of one kind, per-id when the batch route is withheld."""
    wanted = [to_id(i) for i in ids]
    # One id never needs the batch route, and asking for it would trade a working
    # request for a guaranteed 403 on restricted apps.
    if len(wanted) == 1:
        return [single(wanted[0])]

    if kind not in _batch_withheld:
        try:
            result = batch(wanted)
            return [obj for obj in (result.get(f"{kind}s") or []) if obj]
        except SpotifyException as e:
            if e.http_status != 403:
                raise
            # A failed fallback does not prove the batch endpoint is withheld.
            items = [single(i) for i in wanted]
            _batch_withheld.add(kind)
            logging.getLogger(__name__).info(
                f"Spotify withheld batch {kind} reads (HTTP {e.http_status}); "
                f"falling back to one request per {kind}"
            )
            return items

    return [single(i) for i in wanted]


def get_tracks(sp: spotipy.Spotify, track_ids: list[str]) -> list[dict]:
    """Read several tracks in one request where Spotify allows it."""
    return _batch_read("track", track_ids, batch=sp.tracks, single=sp.track)


def get_artists(sp: spotipy.Spotify, artist_ids: list[str]) -> list[dict]:
    """Read several artists in one request where Spotify allows it."""
    return _batch_read("artist", artist_ids, batch=sp.artists, single=sp.artist)


def get_albums(sp: spotipy.Spotify, album_ids: list[str]) -> list[dict]:
    """Read several albums in one request where Spotify allows it."""
    return _batch_read("album", album_ids, batch=sp.albums, single=sp.album)


def playlist_items(
    sp: spotipy.Spotify, playlist_id: str, *, limit: int, offset: int
) -> dict:
    """Read a page of playlist entries. Restricted serves /items, legacy /tracks."""
    pid = to_id(playlist_id)
    return with_fallback(
        "playlist-items",
        lambda: sp._get(f"playlists/{pid}/items", limit=limit, offset=offset),
        lambda: sp.playlist_tracks(pid, limit=limit, offset=offset),
    )


def playlist_total(sp: spotipy.Spotify, playlist_id: str) -> int | None:
    """How many entries a playlist holds, or None if Spotify won't say.

    `playlist(fields="tracks.total")` is the obvious source and does not work:
    restricted apps get the field stripped, so it comes back absent (asking for
    it alone yields a bare `{}`). A one-item page of the items endpoint still
    reports the real `total`, so read it from there.
    """
    page = playlist_items(sp, playlist_id, limit=1, offset=0)
    total = page.get("total")
    return total if isinstance(total, int) else None


def create_playlist(
    sp: spotipy.Spotify, name: str, description: str, public: bool
) -> dict:
    """Restricted moved playlist creation from /users/{id}/playlists to /me/playlists."""
    body = {"name": name, "public": public, "description": description}
    return with_fallback(
        "create-playlist",
        lambda: sp._post("me/playlists", payload=body),
        lambda: sp._post(f"users/{sp.current_user()['id']}/playlists", payload=body),
    )


def playlist_add_items(
    sp: spotipy.Spotify, playlist_id: str, uris: list[str], position: int | None = None
) -> dict:
    pid = to_id(playlist_id)
    body: dict = {"uris": uris}
    if position is not None:
        body["position"] = position
    return with_fallback(
        "playlist-items",
        lambda: sp._post(f"playlists/{pid}/items", payload=body),
        lambda: sp._post(f"playlists/{pid}/tracks", payload=body),
    )


def playlist_remove_items(
    sp: spotipy.Spotify, playlist_id: str, uris: list[str]
) -> dict:
    """Restricted keys the DELETE body off `items`; legacy off `tracks`."""
    pid = to_id(playlist_id)
    entries = [{"uri": uri} for uri in uris]
    return with_fallback(
        "playlist-items",
        lambda: sp._delete(f"playlists/{pid}/items", payload={"items": entries}),
        lambda: sp._delete(f"playlists/{pid}/tracks", payload={"tracks": entries}),
    )


def playlist_reorder_items(
    sp: spotipy.Spotify,
    playlist_id: str,
    *,
    range_start: int,
    insert_before: int,
    range_length: int = 1,
    snapshot_id: str | None = None,
) -> dict:
    pid = to_id(playlist_id)
    body: dict = {
        "range_start": range_start,
        "insert_before": insert_before,
        "range_length": range_length,
    }
    if snapshot_id:
        body["snapshot_id"] = snapshot_id
    return with_fallback(
        "playlist-items",
        lambda: sp._put(f"playlists/{pid}/items", payload=body),
        lambda: sp._put(f"playlists/{pid}/tracks", payload=body),
    )


# /me/library takes its URIs as a query parameter: a JSON body is rejected with
# 400 "Missing required field: uris" (verified live). The legacy routes keep
# taking a JSON body of bare ids.
def save_tracks(sp: spotipy.Spotify, track_ids: list[str]) -> None:
    """Restricted consolidated library writes onto /me/library, keyed by URI."""
    ids = [to_id(t) for t in track_ids]
    uris = ",".join(to_uri("track", i) for i in ids)
    with_fallback(
        "library-write",
        lambda: sp._put("me/library", uris=uris),
        lambda: sp._put("me/tracks", payload={"ids": ids}),
    )


def remove_saved_tracks(sp: spotipy.Spotify, track_ids: list[str]) -> None:
    ids = [to_id(t) for t in track_ids]
    uris = ",".join(to_uri("track", i) for i in ids)
    with_fallback(
        "library-write",
        lambda: sp._delete("me/library", uris=uris),
        lambda: sp._delete("me/tracks", payload={"ids": ids}),
    )


def unfollow_playlist(sp: spotipy.Spotify, playlist_id: str) -> None:
    """Restricted folds playlist follows into /me/library; legacy has its own route."""
    pid = to_id(playlist_id)
    with_fallback(
        "library-write",
        lambda: sp._delete("me/library", uris=to_uri("playlist", pid)),
        lambda: sp._delete(f"playlists/{pid}/followers"),
    )


def saved_tracks_contains(sp: spotipy.Spotify, track_ids: list[str]) -> list[bool]:
    """Which of these tracks are already in the library, one request for up to 50."""
    ids = [to_id(t) for t in track_ids]
    uris = ",".join(to_uri("track", i) for i in ids)
    joined = ",".join(ids)
    return with_fallback(
        "library-read",
        lambda: sp._get("me/library/contains", uris=uris),
        lambda: sp._get("me/tracks/contains", ids=joined),
    )


def saved_albums_contains(sp: spotipy.Spotify, album_ids: list[str]) -> list[bool]:
    """Which of these albums are already saved, one request for up to 20."""
    ids = [to_id(a) for a in album_ids]
    uris = ",".join(to_uri("album", i) for i in ids)
    joined = ",".join(ids)
    return with_fallback(
        "library-read",
        lambda: sp._get("me/library/contains", uris=uris),
        lambda: sp._get("me/albums/contains", ids=joined),
    )


def following_artists_contains(
    sp: spotipy.Spotify, artist_ids: list[str]
) -> list[bool]:
    """Which of these artists the user follows. Follows are not part of /me/library."""
    ids = [to_id(a) for a in artist_ids]
    result: list[bool] = sp._get(
        "me/following/contains", type="artist", ids=",".join(ids)
    )
    return result


# Search page size is regime-dependent: legacy apps get 50, restricted apps get
# 10 and answer 400 "Invalid limit" for anything above it. Which one we are is
# not introspectable, so discover the ceiling on the first rejection and reuse it
# for the life of the process, the same way `with_fallback` caches a family.
_RESTRICTED_SEARCH_LIMIT = 10
SEARCH_LIMIT_MAX = 50
_search_limit_max: int = SEARCH_LIMIT_MAX


def search(
    sp: spotipy.Spotify, query: str, *, qtype: str, limit: int, offset: int
) -> dict:
    """Search, retrying at the restricted page size if the limit is rejected."""
    global _search_limit_max

    capped = min(limit, _search_limit_max)
    try:
        result: dict = sp.search(q=query, type=qtype, limit=capped, offset=offset)
        return result
    except SpotifyException as e:
        # Spotipy prefixes the upstream message with the response URL, whose
        # query string contains "limit" even for unrelated search errors.
        message = (e.msg or "").split("\n", 1)[-1].strip().lower()
        too_large = capped > _RESTRICTED_SEARCH_LIMIT
        if e.http_status != 400 or message != "invalid limit" or not too_large:
            raise
        retried: dict = sp.search(
            q=query, type=qtype, limit=_RESTRICTED_SEARCH_LIMIT, offset=offset
        )
        _search_limit_max = _RESTRICTED_SEARCH_LIMIT
        logging.getLogger(__name__).info(
            f"Spotify rejected search limit {capped}; "
            f"this app caps search at {_RESTRICTED_SEARCH_LIMIT} results per page"
        )
        return retried
