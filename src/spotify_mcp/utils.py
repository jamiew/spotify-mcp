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
