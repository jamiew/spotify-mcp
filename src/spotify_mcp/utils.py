from __future__ import annotations

from urllib.parse import urlparse, urlunparse


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


def to_id(value: str) -> str:
    """Accept a bare Spotify ID, a `spotify:type:id` URI, or an open.spotify.com URL."""
    value = value.strip()
    if value.startswith("spotify:"):
        return value.rsplit(":", 1)[-1]
    if "open.spotify.com/" in value:
        return urlparse(value).path.rsplit("/", 1)[-1].split("?")[0]
    return value


def to_uri(kind: str, value: str) -> str:
    """Build a `spotify:{kind}:{id}` URI from anything `to_id` accepts."""
    return f"spotify:{kind}:{to_id(value)}"
