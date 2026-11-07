from __future__ import annotations

import logging
import os
from functools import partial
from typing import TYPE_CHECKING

import spotipy
from dotenv import load_dotenv
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .errors import ModelVisibleError
from .utils import normalize_redirect_uri, to_id, to_uri

if TYPE_CHECKING:
    from collections.abc import Callable


def load_config() -> dict[str, str | None]:
    """Load configuration from environment variables, then a .env file."""
    load_dotenv()

    return {
        "CLIENT_ID": os.getenv("SPOTIFY_CLIENT_ID"),
        "CLIENT_SECRET": os.getenv("SPOTIFY_CLIENT_SECRET"),
        "REDIRECT_URI": os.getenv("SPOTIFY_REDIRECT_URI"),
    }


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
    # Artist follows (including the consolidated library membership route).
    # /me/library writes list user-follow-modify alongside user-library-modify.
    "user-follow-read",
    "user-follow-modify",
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


class LazyOAuth:
    """Builds SpotifyOAuth on the first token request, not at startup.

    Clients and registries start the server and list its tools without any
    credentials, so a missing one must fail the tool call that needs it.
    """

    def __init__(self) -> None:
        self._oauth: SpotifyOAuth | None = None

    @property
    def oauth(self) -> SpotifyOAuth:
        if self._oauth is None:
            settings = {
                "SPOTIFY_CLIENT_ID": CLIENT_ID,
                "SPOTIFY_CLIENT_SECRET": CLIENT_SECRET,
                "SPOTIFY_REDIRECT_URI": REDIRECT_URI,
            }
            missing = [name for name, value in settings.items() if not value]
            if missing:
                raise ModelVisibleError(
                    f"Spotify credentials are not configured: set {', '.join(missing)}"
                    " in the MCP server's environment, then restart it."
                )
            self._oauth = SpotifyOAuth(
                scope=",".join(SCOPES),
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
            )
        return self._oauth

    def get_access_token(self, as_dict: bool = False) -> str:
        return self.oauth.get_access_token(as_dict=as_dict)  # type: ignore[no-any-return]


def create_client() -> spotipy.Spotify:
    """Build the spotipy client; credentials are checked on its first request.

    Tools in `fastmcp_server` call it directly, except for the operations whose
    path differs between the two 2026 API regimes; those go through the
    `with_fallback` helpers below.
    """
    return spotipy.Spotify(
        auth_manager=LazyOAuth(), status_forcelist=RETRY_STATUS_CODES
    )


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
BATCH_LIMITS: dict[str, int] = {
    "track": 50,
    "artist": 50,
    "album": 20,
    "show": 50,
    "episode": 50,
    "audiobook": 50,
}
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
    """Read a page of playlist entries. Restricted serves /items, legacy /tracks.

    Both routes return podcast episodes in playlists only when additional_types
    asks for them. spotipy's `playlist_tracks` asks for tracks alone, and its
    `playlist_items` targets /items, so the legacy path is spelled out here.
    """
    pid = to_id(playlist_id)
    params = {"limit": limit, "offset": offset, "additional_types": "track,episode"}
    return with_fallback(
        "playlist-items",
        lambda: sp._get(f"playlists/{pid}/items", **params),
        lambda: sp._get(f"playlists/{pid}/tracks", **params),
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
_LIBRARY_BATCH_LIMIT = 40


def save_tracks(sp: spotipy.Spotify, track_ids: list[str]) -> None:
    """Save up to 50 tracks in bounded requests; propagate any failed chunk."""
    for start in range(0, len(track_ids), _LIBRARY_BATCH_LIMIT):
        ids = [to_id(t) for t in track_ids[start : start + _LIBRARY_BATCH_LIMIT]]
        uris = ",".join(to_uri("track", i) for i in ids)
        with_fallback(
            "library-write",
            partial(sp._put, "me/library", uris=uris),
            partial(sp._put, "me/tracks", payload={"ids": ids}),
        )


def remove_saved_tracks(sp: spotipy.Spotify, track_ids: list[str]) -> None:
    """Remove up to 50 tracks in bounded requests; earlier chunks are not rolled back."""
    for start in range(0, len(track_ids), _LIBRARY_BATCH_LIMIT):
        ids = [to_id(t) for t in track_ids[start : start + _LIBRARY_BATCH_LIMIT]]
        uris = ",".join(to_uri("track", i) for i in ids)
        with_fallback(
            "library-write",
            partial(sp._delete, "me/library", uris=uris),
            partial(sp._delete, "me/tracks", payload={"ids": ids}),
        )


def unfollow_playlist(sp: spotipy.Spotify, playlist_id: str) -> None:
    """Restricted folds playlist follows into /me/library; legacy has its own route."""
    pid = to_id(playlist_id)
    with_fallback(
        "library-write",
        lambda: sp._delete("me/library", uris=to_uri("playlist", pid)),
        lambda: sp._delete(f"playlists/{pid}/followers"),
    )


# Legacy routes for the library kinds that never had a /me/library shape. Every
# one takes a query string of bare ids (shows and audiobooks document no body at
# all); albums cap that query at 20, which the album tools already enforce.
_LEGACY_LIBRARY_ROUTES: dict[str, tuple[str, dict[str, str]]] = {
    "album": ("me/albums", {}),
    "artist": ("me/following", {"type": "artist"}),
    "show": ("me/shows", {}),
    "episode": ("me/episodes", {}),
    "audiobook": ("me/audiobooks", {}),
}

# The /me/library reference lists artist URIs for `contains` but not for PUT or
# DELETE, while the February 2026 changelog names /me/library as the replacement
# for PUT/DELETE /me/following. Keep artist writes in their own family so an
# artist-specific rejection cannot pin every other library write to legacy.
_LIBRARY_WRITE_FAMILY = {"artist": "follow-write"}

# /me/library answers 500/502 for `spotify:audiobook:` URIs but accepts the same
# id as `spotify:show:` (verified live, Nov 2026). Audiobooks are shows there.
_LIBRARY_URI_KIND = {"audiobook": "show"}


def _library_uris(kind: str, ids: list[str]) -> str:
    """The comma-separated URIs /me/library expects for `ids` of `kind`."""
    uri_kind = _LIBRARY_URI_KIND.get(kind, kind)
    return ",".join(to_uri(uri_kind, i) for i in ids)


def _library_write(
    sp: spotipy.Spotify, method: str, kind: str, item_ids: list[str]
) -> None:
    """PUT or DELETE library items in chunks of 40; propagate any failed chunk."""
    send = sp._put if method == "PUT" else sp._delete
    legacy_path, legacy_params = _LEGACY_LIBRARY_ROUTES[kind]
    family = _LIBRARY_WRITE_FAMILY.get(kind, "library-write")
    for start in range(0, len(item_ids), _LIBRARY_BATCH_LIMIT):
        ids = [to_id(i) for i in item_ids[start : start + _LIBRARY_BATCH_LIMIT]]
        uris = _library_uris(kind, ids)
        with_fallback(
            family,
            partial(send, "me/library", uris=uris),
            partial(send, legacy_path, ids=",".join(ids), **legacy_params),
        )


def save_albums(sp: spotipy.Spotify, album_ids: list[str]) -> None:
    """Save albums to the library; earlier chunks are not rolled back on failure."""
    _library_write(sp, "PUT", "album", album_ids)


def remove_saved_albums(sp: spotipy.Spotify, album_ids: list[str]) -> None:
    """Remove saved albums; earlier chunks are not rolled back on failure."""
    _library_write(sp, "DELETE", "album", album_ids)


def follow_artists(sp: spotipy.Spotify, artist_ids: list[str]) -> None:
    """Follow artists; earlier chunks are not rolled back on failure."""
    _library_write(sp, "PUT", "artist", artist_ids)


def unfollow_artists(sp: spotipy.Spotify, artist_ids: list[str]) -> None:
    """Unfollow artists; earlier chunks are not rolled back on failure."""
    _library_write(sp, "DELETE", "artist", artist_ids)


def save_shows(sp: spotipy.Spotify, show_ids: list[str]) -> None:
    """Save podcast shows; earlier chunks are not rolled back on failure."""
    _library_write(sp, "PUT", "show", show_ids)


def remove_saved_shows(sp: spotipy.Spotify, show_ids: list[str]) -> None:
    """Remove saved podcast shows; earlier chunks are not rolled back on failure."""
    _library_write(sp, "DELETE", "show", show_ids)


def save_episodes(sp: spotipy.Spotify, episode_ids: list[str]) -> None:
    """Save podcast episodes; earlier chunks are not rolled back on failure."""
    _library_write(sp, "PUT", "episode", episode_ids)


def remove_saved_episodes(sp: spotipy.Spotify, episode_ids: list[str]) -> None:
    """Remove saved podcast episodes; earlier chunks are not rolled back on failure."""
    _library_write(sp, "DELETE", "episode", episode_ids)


def save_audiobooks(sp: spotipy.Spotify, audiobook_ids: list[str]) -> None:
    """Save audiobooks; earlier chunks are not rolled back on failure."""
    _library_write(sp, "PUT", "audiobook", audiobook_ids)


def remove_saved_audiobooks(sp: spotipy.Spotify, audiobook_ids: list[str]) -> None:
    """Remove saved audiobooks; earlier chunks are not rolled back on failure."""
    _library_write(sp, "DELETE", "audiobook", audiobook_ids)


def follow_playlist(sp: spotipy.Spotify, playlist_id: str) -> None:
    """Restricted folds playlist follows into /me/library; legacy has its own route.

    The legacy route takes an optional `public` body that defaults to true; none
    is sent, so both regimes leave visibility to Spotify's default.
    """
    pid = to_id(playlist_id)
    with_fallback(
        "library-write",
        lambda: sp._put("me/library", uris=to_uri("playlist", pid)),
        lambda: sp._put(f"playlists/{pid}/followers"),
    )


def _library_contains(
    sp: spotipy.Spotify, kind: str, item_ids: list[str]
) -> list[bool]:
    """Combine bounded membership reads without shifting incomplete answers."""
    results: list[bool] = []
    for start in range(0, len(item_ids), _LIBRARY_BATCH_LIMIT):
        ids = [to_id(i) for i in item_ids[start : start + _LIBRARY_BATCH_LIMIT]]
        uris = _library_uris(kind, ids)
        legacy_path = f"me/{kind}s/contains"
        legacy_params = {"ids": ",".join(ids)}
        if kind == "artist":
            legacy_path = "me/following/contains"
            legacy_params["type"] = "artist"
        flags: list[bool] = with_fallback(
            "library-read",
            partial(sp._get, "me/library/contains", uris=uris),
            partial(sp._get, legacy_path, **legacy_params),
        )
        if len(flags) != len(ids):
            raise ValueError("Spotify returned an incomplete membership response")
        results.extend(flags)
    return results


def saved_tracks_contains(sp: spotipy.Spotify, track_ids: list[str]) -> list[bool]:
    """Which tracks are saved, with at most 40 URIs per upstream request."""
    return _library_contains(sp, "track", track_ids)


def saved_albums_contains(sp: spotipy.Spotify, album_ids: list[str]) -> list[bool]:
    """Which of up to 20 albums are saved, preserving requested order."""
    return _library_contains(sp, "album", album_ids)


def saved_shows_contains(sp: spotipy.Spotify, show_ids: list[str]) -> list[bool]:
    """Which podcast shows are saved, with at most 40 URIs per upstream request."""
    return _library_contains(sp, "show", show_ids)


def saved_episodes_contains(sp: spotipy.Spotify, episode_ids: list[str]) -> list[bool]:
    """Which podcast episodes are saved, with at most 40 URIs per upstream request."""
    return _library_contains(sp, "episode", episode_ids)


def saved_audiobooks_contains(
    sp: spotipy.Spotify, audiobook_ids: list[str]
) -> list[bool]:
    """Which audiobooks are saved, with at most 40 URIs per upstream request."""
    return _library_contains(sp, "audiobook", audiobook_ids)


def following_artists_contains(
    sp: spotipy.Spotify, artist_ids: list[str]
) -> list[bool]:
    """Check artist follows through consolidated library or legacy follow routes."""
    return _library_contains(sp, "artist", artist_ids)


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
