"""Tests for Spotify API client."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests
import spotipy
from spotipy import SpotifyException
from spotipy.exceptions import SpotifyOauthError

import spotify_mcp.spotify_api as spotify_api
from spotify_mcp.spotify_api import (
    RETRY_STATUS_CODES,
    SCOPES,
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
        spotify_api._batch_withheld.clear()
        yield
        spotify_api._batch_withheld.clear()

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

    def test_falls_back_to_per_id_reads_when_withheld(self):
        sp = MagicMock()
        sp.tracks.side_effect = self.FORBIDDEN
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

    def test_failed_fallback_does_not_disable_recovered_batch_reads(self):
        sp = MagicMock()
        expected = [{"id": "t1"}, {"id": "t2"}]
        sp.tracks.side_effect = [self.FORBIDDEN, {"tracks": expected}]
        error = SpotifyException(401, -1, "Expired token")
        sp.track.side_effect = error

        with pytest.raises(SpotifyException) as raised:
            get_tracks(sp, ["t1", "t2"])
        assert raised.value is error

        assert get_tracks(sp, ["t1", "t2"]) == expected
        assert sp.tracks.call_count == 2

    @pytest.mark.parametrize("status", [401, 429, 500])
    def test_other_batch_errors_propagate(self, status):
        sp = MagicMock()
        error = SpotifyException(status, -1, "Request failed", reason="TEST_REASON")
        sp.tracks.side_effect = error

        with pytest.raises(SpotifyException) as raised:
            get_tracks(sp, ["t1", "t2"])
        assert raised.value is error

        sp.track.assert_not_called()

    def test_accepts_uris_as_well_as_ids(self):
        sp = MagicMock()
        sp.tracks.return_value = {"tracks": [{"id": "t1"}, {"id": "t2"}]}

        get_tracks(sp, ["spotify:track:t1", "t2"])

        sp.tracks.assert_called_once_with(["t1", "t2"])

    def test_a_withheld_kind_does_not_disable_the_others(self):
        sp = MagicMock()
        sp.tracks.side_effect = self.FORBIDDEN
        sp.track.side_effect = [{"id": "t1"}, {"id": "t2"}]
        sp.artists.return_value = {"artists": [{"id": "a1"}, {"id": "a2"}]}

        get_tracks(sp, ["t1", "t2"])

        # tracks degraded, but artists must still be read in one request
        assert spotify_api.get_artists(sp, ["a1", "a2"]) == [{"id": "a1"}, {"id": "a2"}]
        sp.artists.assert_called_once_with(["a1", "a2"])
        sp.artist.assert_not_called()

    def test_albums_batch_through_the_album_route(self):
        sp = MagicMock()
        sp.albums.return_value = {"albums": [{"id": "al1"}, {"id": "al2"}]}

        assert spotify_api.get_albums(sp, ["spotify:album:al1", "al2"]) == [
            {"id": "al1"},
            {"id": "al2"},
        ]
        sp.albums.assert_called_once_with(["al1", "al2"])


class TestSearchLimitCeiling:
    """Exercise search fallback through Spotipy's real HTTP error translation."""

    INVALID_LIMIT = {"error": {"message": "Invalid limit"}}

    def _client(self, *responses):
        pending = iter(responses)
        session = requests.Session()

        def respond(method, url, **kwargs):
            status, payload = next(pending)
            response = requests.Response()
            response.status_code = status
            response.url = (
                requests.Request(method, url, params=kwargs["params"]).prepare().url
            )
            response._content = json.dumps(payload).encode()
            return response

        session.request = MagicMock(side_effect=respond)
        return spotipy.Spotify(
            auth="test-token", requests_session=session
        ), session.request

    @pytest.fixture(autouse=True)
    def _reset_ceiling(self):
        spotify_api._search_limit_max = spotify_api.SEARCH_LIMIT_MAX
        yield
        spotify_api._search_limit_max = spotify_api.SEARCH_LIMIT_MAX

    def test_remembers_the_cap_only_after_a_successful_retry(self):
        first_page = {"tracks": {"items": [], "limit": 10, "offset": 30, "total": 100}}
        next_page = {"tracks": {"items": [], "limit": 10, "offset": 40, "total": 100}}
        sp, request = self._client(
            (400, self.INVALID_LIMIT), (200, first_page), (200, next_page)
        )

        result = spotify_api.search(sp, "trance", qtype="track", limit=20, offset=30)
        assert result == first_page
        result = spotify_api.search(sp, "goa", qtype="track", limit=50, offset=40)
        assert result == next_page
        assert [
            (c.kwargs["params"]["limit"], c.kwargs["params"]["offset"])
            for c in request.call_args_list
        ] == [(20, 30), (10, 30), (10, 40)]

    def test_a_limit_already_at_the_cap_is_not_retried(self):
        sp, request = self._client((400, self.INVALID_LIMIT))

        with pytest.raises(SpotifyException) as caught:
            spotify_api.search(sp, "trance", qtype="track", limit=10, offset=0)

        assert caught.value.http_status == 400
        assert caught.value.msg.endswith("\n Invalid limit")
        assert request.call_count == 1

    @pytest.mark.parametrize(
        ("status", "message"),
        [
            (400, "Invalid query"),
            (400, "Query exceeds limit"),
            (401, "Invalid limit"),
            (403, "Invalid limit"),
            (429, "Invalid limit"),
            (500, "Invalid limit"),
            (503, "Invalid limit"),
        ],
    )
    def test_unrelated_errors_preserve_reason_and_do_not_reduce_later_limits(
        self, status, message
    ):
        error = {"error": {"message": message, "reason": "upstream-reason"}}
        page = {"tracks": {"items": [], "limit": 50, "offset": 20}}
        sp, request = self._client((status, error), (200, page))

        with pytest.raises(SpotifyException) as caught:
            spotify_api.search(sp, "Invalid limit", qtype="track", limit=20, offset=0)

        assert caught.value.http_status == status
        assert caught.value.reason == "upstream-reason"
        assert "limit=20" in caught.value.msg
        assert caught.value.msg.endswith(f"\n {message}")
        assert request.call_count == 1
        assert (
            spotify_api.search(sp, "trance", qtype="track", limit=50, offset=20) == page
        )
        assert request.call_args.kwargs["params"]["limit"] == 50

    def test_failed_fallback_does_not_poison_the_ceiling(self):
        error = {"error": {"message": "Invalid query", "reason": "bad-query"}}
        page = {"tracks": {"items": [], "limit": 50, "offset": 30}}
        sp, request = self._client((400, self.INVALID_LIMIT), (400, error), (200, page))

        with pytest.raises(SpotifyException) as caught:
            spotify_api.search(sp, "trance", qtype="track", limit=20, offset=30)

        assert caught.value.http_status == 400
        assert caught.value.reason == "bad-query"
        assert caught.value.msg.endswith("\n Invalid query")
        assert spotify_api.search(sp, "goa", qtype="track", limit=50, offset=30) == page
        assert [
            (c.kwargs["params"]["limit"], c.kwargs["params"]["offset"])
            for c in request.call_args_list
        ] == [(20, 30), (10, 30), (50, 30)]


class TestLibraryWrites:
    """The restricted /me/library route takes its URIs as a query parameter; a
    JSON body answers 400 "Missing required field: uris" against the live API,
    and the legacy routes take a body of bare ids."""

    def test_save_tracks_prefers_the_restricted_library_route(self):
        sp = MagicMock()

        save_tracks(sp, ["abc", "spotify:track:def"])

        sp._put.assert_called_once_with(
            "me/library", uris="spotify:track:abc,spotify:track:def"
        )

    def test_save_tracks_falls_back_to_the_legacy_tracks_route(self):
        sp = MagicMock()
        sp._put.side_effect = [SpotifyException(400, -1, "bad request"), None]

        save_tracks(sp, ["abc"])

        assert sp._put.call_args_list[-1].args == ("me/tracks",)
        assert sp._put.call_args_list[-1].kwargs == {"payload": {"ids": ["abc"]}}

    def test_remove_saved_tracks_prefers_the_restricted_library_route(self):
        sp = MagicMock()

        remove_saved_tracks(sp, ["abc"])

        sp._delete.assert_called_once_with("me/library", uris="spotify:track:abc")

    def test_remove_saved_tracks_falls_back_to_the_legacy_tracks_route(self):
        sp = MagicMock()
        sp._delete.side_effect = [SpotifyException(400, -1, "bad request"), None]

        remove_saved_tracks(sp, ["abc"])

        assert sp._delete.call_args_list[-1].args == ("me/tracks",)
        assert sp._delete.call_args_list[-1].kwargs == {"payload": {"ids": ["abc"]}}

    def test_unfollow_playlist_falls_back_to_the_followers_route(self):
        sp = MagicMock()
        sp._delete.side_effect = [SpotifyException(404, -1, "not served"), None]

        spotify_api.unfollow_playlist(sp, "spotify:playlist:pl1")

        assert [c.args[0] for c in sp._delete.call_args_list] == [
            "me/library",
            "playlists/pl1/followers",
        ]


class TestMembershipReads:
    def test_saved_tracks_contains_prefers_the_restricted_route(self):
        sp = MagicMock()
        sp._get.return_value = [True, False]

        assert spotify_api.saved_tracks_contains(sp, ["abc", "def"]) == [True, False]

        sp._get.assert_called_once_with(
            "me/library/contains", uris="spotify:track:abc,spotify:track:def"
        )

    def test_saved_tracks_contains_falls_back_to_the_legacy_route(self):
        sp = MagicMock()
        sp._get.side_effect = [SpotifyException(400, -1, "bad request"), [True]]

        assert spotify_api.saved_tracks_contains(sp, ["abc"]) == [True]

        assert sp._get.call_args_list[-1].args == ("me/tracks/contains",)
        assert sp._get.call_args_list[-1].kwargs == {"ids": "abc"}

    def test_saved_albums_contains_uses_the_album_routes(self):
        sp = MagicMock()
        sp._get.side_effect = [SpotifyException(400, -1, "bad request"), [False]]

        assert spotify_api.saved_albums_contains(sp, ["spotify:album:al1"]) == [False]

        assert [c.args[0] for c in sp._get.call_args_list] == [
            "me/library/contains",
            "me/albums/contains",
        ]

    def test_following_artists_prefers_the_library_route(self):
        sp = MagicMock()
        sp._get.return_value = [True]

        assert spotify_api.following_artists_contains(sp, ["a1"]) == [True]

        sp._get.assert_called_once_with("me/library/contains", uris="spotify:artist:a1")

    def test_following_artists_falls_back_to_the_follow_route(self):
        sp = MagicMock()
        # A legacy app is rejected by the library route and served by the follow
        # route; a restricted app is the other way round and never gets here.
        sp._get.side_effect = [SpotifyException(400, -1, "bad request"), [False]]

        assert spotify_api.following_artists_contains(sp, ["spotify:artist:a1"]) == [
            False
        ]

        assert [c.args[0] for c in sp._get.call_args_list] == [
            "me/library/contains",
            "me/following/contains",
        ]


class TestScopes:
    def test_follow_read_scope_is_requested(self):
        # check_following_artists reads /me/following/contains, which Spotify gates
        # behind user-follow-read. Without the scope the route answers a bare 403
        # with no reason, so the tool fails for every app and every regime.
        assert "user-follow-read" in SCOPES

    def test_every_scope_is_declared_once(self):
        assert len(SCOPES) == len(set(SCOPES))


class TestRetryPolicy:
    def test_429_is_not_retried(self):
        # Quota is counted per developer account, so retrying burns every app's pool
        assert 429 not in RETRY_STATUS_CODES
        assert 503 in RETRY_STATUS_CODES
