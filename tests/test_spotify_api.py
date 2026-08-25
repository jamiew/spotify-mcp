"""Tests for Spotify API client."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import spotipy
from spotipy import SpotifyException
from spotipy.exceptions import SpotifyOauthError

import spotify_mcp.spotify_api as spotify_api
from spotify_mcp.spotify_api import (
    RETRY_STATUS_CODES,
    Client,
    _legacy_families,
    get_tracks,
    load_config,
    remove_saved_tracks,
    save_tracks,
    with_fallback,
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


class TestWithFallback:
    """The Feb 2026 regime fallback. These are the paths a fake upstream can't
    reach, so they're asserted directly rather than through a tool."""

    def test_uses_restricted_shape_when_it_works(self):
        restricted = MagicMock(return_value="restricted")
        legacy = MagicMock()

        assert with_fallback("fam", restricted, legacy) == "restricted"
        legacy.assert_not_called()

    @pytest.mark.parametrize("status", [400, 404, 405, 410])
    def test_falls_back_on_a_regime_miss(self, status):
        restricted = MagicMock(side_effect=SpotifyException(status, -1, "nope"))
        legacy = MagicMock(return_value="legacy")

        assert with_fallback("fam", restricted, legacy) == "legacy"

    def test_caches_the_resolved_regime(self):
        restricted = MagicMock(side_effect=SpotifyException(404, -1, "nope"))
        legacy = MagicMock(return_value="legacy")

        with_fallback("fam", restricted, legacy)
        with_fallback("fam", restricted, legacy)

        # the restricted shape is not retried once the family has resolved
        assert restricted.call_count == 1
        assert legacy.call_count == 2

    def test_a_genuine_not_found_does_not_pin_the_regime(self):
        # Failing both ways is a real 404, not a regime miss — don't cache it.
        restricted = MagicMock(side_effect=SpotifyException(404, -1, "nope"))
        legacy = MagicMock(side_effect=SpotifyException(404, -1, "nope"))

        with pytest.raises(SpotifyException):
            with_fallback("fam", restricted, legacy)

        assert "fam" not in _legacy_families

    def test_other_errors_propagate_without_a_retry(self):
        restricted = MagicMock(side_effect=SpotifyException(403, -1, "forbidden"))
        legacy = MagicMock()

        with pytest.raises(SpotifyException):
            with_fallback("fam", restricted, legacy)

        legacy.assert_not_called()


class TestGetTracks:
    """Batch track reads are withheld from restricted apps (403) while single
    reads work. No fake upstream reports that, so it is asserted directly."""

    FORBIDDEN = SpotifyException(403, -1, "Forbidden")

    @pytest.fixture(autouse=True)
    def _reset_withheld(self):
        spotify_api._batch_tracks_withheld = False
        yield
        spotify_api._batch_tracks_withheld = False

    def test_a_single_id_never_uses_the_batch_route(self):
        sp = MagicMock()
        sp.track.return_value = {"id": "t1"}

        assert get_tracks(sp, ["t1"]) == [{"id": "t1"}]

        sp.track.assert_called_once_with("t1")
        sp.tracks.assert_not_called()

    def test_batches_when_allowed(self):
        sp = MagicMock()
        sp.tracks.return_value = {"tracks": [{"id": "t1"}, {"id": "t2"}]}

        assert get_tracks(sp, ["t1", "t2"]) == [{"id": "t1"}, {"id": "t2"}]

        sp.tracks.assert_called_once_with(["t1", "t2"])
        sp.track.assert_not_called()

    def test_drops_null_entries_from_a_batch(self):
        sp = MagicMock()
        sp.tracks.return_value = {"tracks": [{"id": "t1"}, None]}

        assert get_tracks(sp, ["t1", "gone"]) == [{"id": "t1"}]

    @pytest.mark.parametrize("status", [401, 403])
    def test_falls_back_to_per_id_reads_when_withheld(self, status):
        sp = MagicMock()
        sp.tracks.side_effect = SpotifyException(status, -1, "Forbidden")
        sp.track.side_effect = [{"id": "t1"}, {"id": "t2"}]

        assert get_tracks(sp, ["t1", "t2"]) == [{"id": "t1"}, {"id": "t2"}]

        assert [c.args[0] for c in sp.track.call_args_list] == ["t1", "t2"]

    def test_remembers_that_batching_is_withheld(self):
        sp = MagicMock()
        sp.tracks.side_effect = self.FORBIDDEN
        sp.track.side_effect = [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}, {"id": "t4"}]

        get_tracks(sp, ["t1", "t2"])
        get_tracks(sp, ["t3", "t4"])

        # the 403 is paid once, not on every call
        assert sp.tracks.call_count == 1
        assert sp.track.call_count == 4
        assert spotify_api.batch_tracks_withheld() is True

    def test_other_batch_errors_propagate(self):
        sp = MagicMock()
        sp.tracks.side_effect = SpotifyException(500, -1, "Server Error")

        with pytest.raises(SpotifyException):
            get_tracks(sp, ["t1", "t2"])

        sp.track.assert_not_called()
        assert spotify_api.batch_tracks_withheld() is False

    def test_accepts_uris_as_well_as_ids(self):
        sp = MagicMock()
        sp.tracks.return_value = {"tracks": [{"id": "t1"}, {"id": "t2"}]}

        get_tracks(sp, ["spotify:track:t1", "t2"])

        sp.tracks.assert_called_once_with(["t1", "t2"])


class TestLibraryWrites:
    def test_save_tracks_prefers_the_restricted_library_route(self):
        sp = MagicMock()

        save_tracks(sp, ["abc", "spotify:track:def"])

        sp._put.assert_called_once_with(
            "me/library",
            payload={"uris": ["spotify:track:abc", "spotify:track:def"]},
        )

    def test_save_tracks_falls_back_to_me_tracks(self):
        sp = MagicMock()
        sp._put.side_effect = SpotifyException(400, -1, "bad request")

        save_tracks(sp, ["abc"])

        sp.current_user_saved_tracks_add.assert_called_once_with(tracks=["abc"])

    def test_remove_saved_tracks_prefers_the_restricted_library_route(self):
        sp = MagicMock()

        remove_saved_tracks(sp, ["abc"])

        sp._delete.assert_called_once_with(
            "me/library", payload={"uris": ["spotify:track:abc"]}
        )


class TestRetryPolicy:
    def test_429_is_not_retried(self):
        # Quota is counted per developer account, so retrying burns every app's pool
        assert 429 not in RETRY_STATUS_CODES
        assert 503 in RETRY_STATUS_CODES
