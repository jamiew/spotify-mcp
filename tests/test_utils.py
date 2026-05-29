"""Tests for utility functions."""

from spotify_mcp.utils import normalize_redirect_uri


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
