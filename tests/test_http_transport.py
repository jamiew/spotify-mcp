"""Tests for streamable-HTTP transport selection and the bearer-auth wrapper."""

import sys
from unittest.mock import MagicMock

import pytest
from starlette.applications import Starlette

import spotify_mcp
from spotify_mcp.fastmcp_server import _bearer_guard, build_http_app


class _FakeApp:
    """Minimal inner ASGI app that records which scope types reach it."""

    def __init__(self):
        self.seen = []

    async def __call__(self, scope, receive, send):
        self.seen.append(scope["type"])
        await send({"type": "passed-through"})


async def _drive(app, scope):
    """Run an ASGI app once and return the messages it sent."""
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _http_scope(headers):
    return {"type": "http", "headers": headers}


class TestBearerGuard:
    async def test_rejects_missing_header(self):
        inner = _FakeApp()
        guarded = _bearer_guard(inner, "secret")

        sent = await _drive(guarded, _http_scope([]))

        assert sent[0]["status"] == 401
        assert inner.seen == []

    async def test_rejects_wrong_token(self):
        inner = _FakeApp()
        guarded = _bearer_guard(inner, "secret")

        sent = await _drive(
            guarded, _http_scope([(b"authorization", b"Bearer nope")])
        )

        assert sent[0]["status"] == 401
        assert inner.seen == []

    async def test_allows_correct_token(self):
        inner = _FakeApp()
        guarded = _bearer_guard(inner, "secret")

        await _drive(guarded, _http_scope([(b"authorization", b"Bearer secret")]))

        assert inner.seen == ["http"]

    async def test_passes_through_non_http_scopes(self):
        # lifespan must reach the inner app so the session manager can start
        inner = _FakeApp()
        guarded = _bearer_guard(inner, "secret")

        await _drive(guarded, {"type": "lifespan", "headers": []})

        assert inner.seen == ["lifespan"]


class TestBuildHttpApp:
    def test_without_bearer_returns_starlette(self):
        assert isinstance(build_http_app(), Starlette)

    async def test_with_bearer_blocks_unauthorized(self):
        app = build_http_app(bearer_token="tok")

        sent = await _drive(app, _http_scope([]))

        assert sent[0]["status"] == 401


class TestEnvBool:
    def test_true_values(self, monkeypatch):
        monkeypatch.setenv("X_FLAG", "Yes")
        assert spotify_mcp._env_bool("X_FLAG") is True

    def test_false_value(self, monkeypatch):
        monkeypatch.setenv("X_FLAG", "0")
        assert spotify_mcp._env_bool("X_FLAG") is False

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("X_FLAG", raising=False)
        assert spotify_mcp._env_bool("X_FLAG", default=True) is True


class TestTransportDispatch:
    def test_defaults_to_stdio(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_MCP_TRANSPORT", raising=False)
        stdio, http = MagicMock(), MagicMock()
        monkeypatch.setattr(spotify_mcp, "_run_stdio", stdio)
        monkeypatch.setattr(spotify_mcp, "_run_http", http)

        spotify_mcp.main()

        stdio.assert_called_once()
        http.assert_not_called()

    def test_selects_http(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_MCP_TRANSPORT", "streamable-http")
        stdio, http = MagicMock(), MagicMock()
        monkeypatch.setattr(spotify_mcp, "_run_stdio", stdio)
        monkeypatch.setattr(spotify_mcp, "_run_http", http)

        spotify_mcp.main()

        http.assert_called_once()
        stdio.assert_not_called()

    def test_unknown_transport_raises(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_MCP_TRANSPORT", "carrier-pigeon")

        with pytest.raises(SystemExit):
            spotify_mcp.main()


class TestRunHttp:
    def test_no_bearer_uses_mcp_run(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_MCP_BEARER", raising=False)
        monkeypatch.setenv("SPOTIFY_MCP_PORT", "9001")
        run = MagicMock()
        monkeypatch.setattr(spotify_mcp.mcp, "run", run)

        spotify_mcp._run_http()

        run.assert_called_once_with(transport="streamable-http")
        assert spotify_mcp.mcp.settings.port == 9001

    def test_bearer_runs_uvicorn(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_MCP_BEARER", "tok")
        fake_uvicorn = MagicMock()
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

        spotify_mcp._run_http()

        fake_uvicorn.run.assert_called_once()
