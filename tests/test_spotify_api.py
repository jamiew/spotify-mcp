"""Tests for Spotify API client."""

import logging
from unittest.mock import patch

import pytest
import spotipy
from spotipy.cache_handler import CacheFileHandler, MemoryCacheHandler
from spotipy.exceptions import SpotifyOauthError

from spotify_mcp.spotify_api import (
    Client,
    build_cache_handler,
    is_headless,
    load_config,
)


class TestLoadConfig:
    """Test configuration loading precedence."""

    @patch.dict(
        "os.environ",
        {
            "SPOTIFY_CLIENT_ID": "env_client_id",
            "SPOTIFY_CLIENT_SECRET": "env_client_secret",
            "SPOTIFY_REDIRECT_URI": "env_redirect_uri",
        },
    )
    def test_load_config_from_env(self):
        config = load_config()

        assert config["CLIENT_ID"] == "env_client_id"
        assert config["CLIENT_SECRET"] == "env_client_secret"
        assert config["REDIRECT_URI"] == "env_redirect_uri"

    @patch.dict("os.environ", {}, clear=True)
    @patch("spotify_mcp.spotify_api.load_dotenv")
    def test_load_config_from_dotenv(self, mock_load_dotenv):
        with patch("os.getenv") as mock_getenv:
            mock_getenv.side_effect = lambda key: {
                "SPOTIFY_CLIENT_ID": "dotenv_client_id",
                "SPOTIFY_CLIENT_SECRET": "dotenv_client_secret",
                "SPOTIFY_REDIRECT_URI": "dotenv_redirect_uri",
            }.get(key)

            config = load_config()

            assert config["CLIENT_ID"] == "dotenv_client_id"
            assert config["CLIENT_SECRET"] == "dotenv_client_secret"
            assert config["REDIRECT_URI"] == "dotenv_redirect_uri"

    @patch.dict("os.environ", {}, clear=True)
    @patch("os.getenv", return_value=None)
    def test_load_config_falls_back_to_pyproject(self, mock_getenv):
        mock_toml_data = {
            "tool": {
                "spotify-mcp": {
                    "env": {
                        "SPOTIFY_CLIENT_ID": "pyproject_client_id",
                        "SPOTIFY_CLIENT_SECRET": "pyproject_client_secret",
                        "SPOTIFY_REDIRECT_URI": "pyproject_redirect_uri",
                    }
                }
            }
        }

        with (
            patch("builtins.open", create=True),
            patch("tomllib.load", return_value=mock_toml_data),
            patch("pathlib.Path.exists", return_value=True),
        ):
            config = load_config()

            assert config["CLIENT_ID"] == "pyproject_client_id"
            assert config["CLIENT_SECRET"] == "pyproject_client_secret"
            assert config["REDIRECT_URI"] == "pyproject_redirect_uri"


class TestSpotifyClient:
    """Test Spotify OAuth client wrapper."""

    def test_initializes_with_authenticated_spotipy_client(self):
        # Test credentials are injected via pytest-env, so construction succeeds.
        client = Client()

        assert isinstance(client.sp, spotipy.Spotify)
        assert client.auth_manager is not None
        assert client.cache_handler is not None

    def test_uses_provided_logger(self):
        custom_logger = logging.getLogger("custom_test_logger")

        client = Client(logger=custom_logger)

        assert client.logger is custom_logger

    @patch("spotify_mcp.spotify_api.CLIENT_ID", None)
    @patch("spotify_mcp.spotify_api.CLIENT_SECRET", "test_client_secret")
    @patch("spotify_mcp.spotify_api.REDIRECT_URI", "test_redirect_uri")
    def test_raises_on_missing_credentials(self):
        with pytest.raises(SpotifyOauthError):
            Client()


class TestHeadlessAuth:
    """Headless/remote OAuth: no browser, token seeded out-of-band."""

    def test_is_headless_false_for_local_stdio(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("SPOTIFY_MCP_TRANSPORT", raising=False)
        assert is_headless() is False

    def test_is_headless_true_with_refresh_token(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "rt")
        assert is_headless() is True

    def test_is_headless_true_with_http_transport(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN", raising=False)
        monkeypatch.setenv("SPOTIFY_MCP_TRANSPORT", "streamable-http")
        assert is_headless() is True

    def test_refresh_token_seeds_memory_handler(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "my-refresh")
        monkeypatch.delenv("SPOTIFY_CACHE_PATH", raising=False)

        handler = build_cache_handler("scope-a,scope-b")

        assert isinstance(handler, MemoryCacheHandler)
        cached = handler.get_cached_token()
        assert cached["refresh_token"] == "my-refresh"
        assert cached["scope"] == "scope-a,scope-b"
        assert cached["expires_at"] == 0

    def test_cache_path_uses_file_handler(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN", raising=False)
        monkeypatch.setenv("SPOTIFY_CACHE_PATH", str(tmp_path / ".cache"))

        handler = build_cache_handler("scope")

        assert isinstance(handler, CacheFileHandler)

    def test_no_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("SPOTIFY_CACHE_PATH", raising=False)

        assert build_cache_handler("scope") is None

    def test_client_disables_browser_when_headless(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "rt")

        with patch("spotify_mcp.spotify_api.SpotifyOAuth") as mock_oauth:
            Client()

        kwargs = mock_oauth.call_args.kwargs
        assert kwargs["open_browser"] is False
        assert isinstance(kwargs["cache_handler"], MemoryCacheHandler)

    def test_client_enables_browser_for_local(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("SPOTIFY_MCP_TRANSPORT", raising=False)

        with patch("spotify_mcp.spotify_api.SpotifyOAuth") as mock_oauth:
            Client()

        assert mock_oauth.call_args.kwargs["open_browser"] is True
