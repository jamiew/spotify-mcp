"""Tests for utility functions."""

from spotify_mcp.utils import normalize_redirect_uri, to_id, to_uri


class TestNormalizeRedirectUri:
    """Test redirect URI normalization."""

    def test_converts_localhost_to_loopback_ip(self):
        result = normalize_redirect_uri("http://localhost:8888/callback")
        assert result == "http://127.0.0.1:8888/callback"

    def test_converts_localhost_without_port(self):
        result = normalize_redirect_uri("http://localhost/callback")
        assert result == "http://127.0.0.1/callback"

    def test_preserves_path_and_scheme(self):
        result = normalize_redirect_uri("https://localhost:9000/auth/spotify")
        assert result == "https://127.0.0.1:9000/auth/spotify"

    def test_leaves_non_localhost_untouched(self):
        url = "https://example.com:443/callback"
        assert normalize_redirect_uri(url) == url

    def test_leaves_existing_loopback_ip_untouched(self):
        url = "http://127.0.0.1:8888/callback"
        assert normalize_redirect_uri(url) == url

    def test_empty_string_returns_empty(self):
        assert normalize_redirect_uri("") == ""

    def test_does_not_rewrite_host_that_only_starts_with_localhost_label(self):
        # "localhostess.com" must not be treated as localhost
        url = "http://localhostess.com/callback"
        assert normalize_redirect_uri(url) == url


class TestToId:
    def test_bare_id_passes_through(self):
        assert to_id("4iV5W9uYEdYUVa79Axb7Rh") == "4iV5W9uYEdYUVa79Axb7Rh"

    def test_strips_uri_prefix(self):
        assert to_id("spotify:track:abc123") == "abc123"

    def test_strips_open_url_and_query(self):
        assert to_id("https://open.spotify.com/playlist/xyz?si=abcd") == "xyz"


class TestToUri:
    def test_builds_from_bare_id(self):
        assert to_uri("track", "abc") == "spotify:track:abc"

    def test_is_idempotent_on_a_uri(self):
        assert to_uri("track", "spotify:track:abc") == "spotify:track:abc"
