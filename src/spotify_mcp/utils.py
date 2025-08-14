import functools
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import quote, urlparse, urlunparse

T = TypeVar("T")


def normalize_redirect_uri(url: str) -> str:
    """Normalize redirect URI to meet Spotify's requirements.

    Converts localhost to 127.0.0.1 for better Spotify API compatibility.
    """
    if not url:
        return url

    parsed = urlparse(url)

    # Convert localhost to 127.0.0.1
    if parsed.netloc == "localhost" or parsed.netloc.startswith("localhost:"):
        port = ""
        if ":" in parsed.netloc:
            port = ":" + parsed.netloc.split(":")[1]
        parsed = parsed._replace(netloc=f"127.0.0.1{port}")

    return urlunparse(parsed)


def parse_track(
    track_item: dict[str, Any] | None, detailed: bool = False
) -> dict[str, Any] | None:
    if not track_item:
        return None
    narrowed_item = {
        "name": track_item["name"],
        "id": track_item["id"],
    }

    if "is_playing" in track_item:
        narrowed_item["is_playing"] = track_item["is_playing"]

    if detailed:
        narrowed_item["album"] = parse_album(track_item.get("album"))
        for k in ["track_number", "duration_ms"]:
            narrowed_item[k] = track_item.get(k)

    if not track_item.get("is_playable", True):
        narrowed_item["is_playable"] = False

    artists = [a["name"] for a in track_item["artists"]]
    if detailed:
        artists = [parse_artist(a) for a in track_item["artists"]]

    if len(artists) == 1:
        narrowed_item["artist"] = artists[0]
    else:
        narrowed_item["artists"] = artists

    return narrowed_item


def parse_artist(
    artist_item: dict[str, Any] | None, detailed: bool = False
) -> dict[str, Any] | None:
    if not artist_item:
        return None
    narrowed_item = {
        "name": artist_item["name"],
        "id": artist_item["id"],
    }
    if detailed:
        narrowed_item["genres"] = artist_item.get("genres")

    return narrowed_item


def parse_playlist(
    playlist_item: dict[str, Any] | None, detailed: bool = False
) -> dict[str, Any] | None:
    if not playlist_item:
        return None
    narrowed_item = {
        "name": playlist_item["name"],
        "id": playlist_item["id"],
        "owner": playlist_item["owner"]["display_name"],
    }
    if detailed:
        narrowed_item["description"] = playlist_item.get("description")
        tracks = []
        for t in playlist_item["tracks"]["items"]:
            tracks.append(parse_track(t["track"]))
        narrowed_item["tracks"] = tracks

    return narrowed_item


def parse_album(
    album_item: dict[str, Any] | None, detailed: bool = False
) -> dict[str, Any] | None:
    if not album_item:
        return None
    narrowed_item = {
        "name": album_item["name"],
        "id": album_item["id"],
    }

    artists = [a["name"] for a in album_item["artists"]]

    if detailed:
        tracks = []
        for t in album_item["tracks"]["items"]:
            tracks.append(parse_track(t))
        narrowed_item["tracks"] = tracks
        artists = [parse_artist(a) for a in album_item["artists"]]

        for k in ["total_tracks", "release_date", "genres"]:
            narrowed_item[k] = album_item.get(k)

    if len(artists) == 1:
        narrowed_item["artist"] = artists[0]
    else:
        narrowed_item["artists"] = artists

    return narrowed_item


def parse_search_results(
    results: dict[str, Any] | None, qtype: str
) -> dict[str, list[dict[str, Any]]]:
    if not results:
        return {}
    _results = defaultdict(list)

    for q in qtype.split(","):
        match q:
            case "track":
                for _idx, item in enumerate(results["tracks"]["items"]):
                    if not item:
                        continue
                    _results["tracks"].append(parse_track(item))
            case "artist":
                for _idx, item in enumerate(results["artists"]["items"]):
                    if not item:
                        continue
                    _results["artists"].append(parse_artist(item))
            case "playlist":
                for _idx, item in enumerate(results["playlists"]["items"]):
                    if not item:
                        continue
                    _results["playlists"].append(parse_playlist(item))
            case "album":
                for _idx, item in enumerate(results["albums"]["items"]):
                    if not item:
                        continue
                    _results["albums"].append(parse_album(item))
            case _:
                raise ValueError(f"uknown qtype {qtype}")

    # Filter out None values and convert to regular dict
    filtered_results = {
        key: [item for item in items if item is not None]
        for key, items in _results.items()
    }
    return dict(filtered_results)


def build_search_query(
    base_query: str,
    artist: str | None = None,
    track: str | None = None,
    album: str | None = None,
    year: str | None = None,
    year_range: tuple[int, int] | None = None,
    # upc: Optional[str] = None,
    # isrc: Optional[str] = None,
    genre: str | None = None,
    is_hipster: bool = False,
    is_new: bool = False,
) -> str:
    """
    Build a search query string with optional filters.

    Args:
        base_query: Base search term
        artist: Artist name filter
        track: Track name filter
        album: Album name filter
        year: Specific year filter
        year_range: Tuple of (start_year, end_year) for year range filter
        genre: Genre filter
        is_hipster: Filter for lowest 10% popularity albums
        is_new: Filter for albums released in past two weeks

    Returns:
        Encoded query string with applied filters
    """
    filters = []

    if artist:
        filters.append(f"artist:{artist}")
    if track:
        filters.append(f"track:{track}")
    if album:
        filters.append(f"album:{album}")
    if year:
        filters.append(f"year:{year}")
    if year_range:
        filters.append(f"year:{year_range[0]}-{year_range[1]}")
    if genre:
        filters.append(f"genre:{genre}")
    if is_hipster:
        filters.append("tag:hipster")
    if is_new:
        filters.append("tag:new")

    query_parts = [base_query] + filters
    return quote(" ".join(query_parts))


def validate[T](func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for Spotify API methods that handles authentication and device validation.
    - Checks and refreshes authentication if needed
    - Validates active device and retries with candidate device if needed
    """

    @functools.wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> T:
        # Handle authentication
        if not self.auth_ok():
            self.auth_refresh()

        # Handle device validation
        if not self.is_active_device():
            kwargs["device"] = self._get_candidate_device()

        return func(self, *args, **kwargs)

    return wrapper
