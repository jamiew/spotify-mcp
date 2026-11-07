"""Turn spotipy failures into errors the model can read and act on."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ToolError

if TYPE_CHECKING:
    from spotipy import SpotifyException


class SpotifyMCPErrorCode(Enum):
    """Error codes for Spotify MCP operations."""

    # Authentication errors
    AUTHENTICATION_FAILED = "authentication_failed"
    TOKEN_EXPIRED = "token_expired"  # nosec B105
    INSUFFICIENT_SCOPE = "insufficient_scope"

    # API errors
    API_RATE_LIMITED = "api_rate_limited"
    API_QUOTA_EXCEEDED = "api_quota_exceeded"
    API_UNAVAILABLE = "api_unavailable"

    # Device errors
    NO_ACTIVE_DEVICE = "no_active_device"
    DEVICE_NOT_FOUND = "device_not_found"
    PREMIUM_REQUIRED = "premium_required"

    # Resource errors
    TRACK_NOT_FOUND = "track_not_found"
    PLAYLIST_NOT_FOUND = "playlist_not_found"
    USER_NOT_FOUND = "user_not_found"

    # Playback errors
    PLAYBACK_RESTRICTED = "playback_restricted"

    # General errors
    UNKNOWN_ERROR = "unknown_error"


class SpotifyMCPError(Exception):
    """A classified Spotify failure: a code, a message and an optional fix."""

    def __init__(
        self,
        code: SpotifyMCPErrorCode,
        message: str,
        suggestion: str | None = None,
        reason: str | None = None,
    ):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        # Spotify's machine-readable `reason` (NO_ACTIVE_DEVICE, QUOTA_EXCEEDED, …).
        # Keep it on every error: dropping it is what makes upstream regime changes
        # near-impossible to diagnose from a tool failure.
        self.reason = reason
        super().__init__(message)

    @classmethod
    def from_spotify_exception(cls, exc: SpotifyException) -> SpotifyMCPError:
        """Classify a spotipy exception, keeping Spotify's `reason`."""
        code, message, suggestion = _classify(exc)
        return cls(code, message, suggestion, getattr(exc, "reason", None) or None)


def _classify(
    exc: SpotifyException,
) -> tuple[SpotifyMCPErrorCode, str, str | None]:
    status_code = getattr(exc, "http_status", None)
    error_message = str(exc)
    lowered = error_message.lower()
    # `reason` is the only reliable discriminator; the prose varies. Reason-keyed
    # cases come first because they are exact where the string matching below
    # is a guess.
    reason = getattr(exc, "reason", None)

    if reason == "NO_ACTIVE_DEVICE":
        return (
            SpotifyMCPErrorCode.NO_ACTIVE_DEVICE,
            "No active Spotify device found",
            "Open Spotify on a device, or use list_devices + transfer_playback",
        )

    if reason == "PREMIUM_REQUIRED":
        return (
            SpotifyMCPErrorCode.PREMIUM_REQUIRED,
            "Spotify Premium is required for this operation",
            "Upgrade to Spotify Premium to use playback features",
        )

    if reason == "QUOTA_EXCEEDED" or "quota" in lowered:
        return (
            SpotifyMCPErrorCode.API_QUOTA_EXCEEDED,
            "Spotify API quota exceeded for this developer account",
            "Development Mode apps share the developer account's quota. "
            "Stop automatic retries and check Spotify's quota guidance; "
            "no reset time is guaranteed",
        )

    # spotipy raises SpotifyException(429, -1, "… Max Retries") when urllib3
    # exhausts its retries, whatever status was actually retried. We only retry
    # 500/502/503/504, so this is an outage, not a quota problem; reporting it
    # as rate limiting sends the caller off to back off on the wrong thing.
    # Check `msg`: `str(exc)` appends ", reason: too many 500 error responses".
    retry_msg = str(getattr(exc, "msg", ""))
    if status_code == 429 and retry_msg.rstrip().endswith("Max Retries"):
        return (
            SpotifyMCPErrorCode.API_UNAVAILABLE,
            "Spotify API is temporarily unavailable (retries exhausted)",
            "Try again in a few minutes",
        )

    if status_code == 401:
        if "token expired" in lowered:
            return (
                SpotifyMCPErrorCode.TOKEN_EXPIRED,
                "Spotify access token has expired",
                "Please re-authenticate with Spotify",
            )
        return (
            SpotifyMCPErrorCode.AUTHENTICATION_FAILED,
            "Authentication with Spotify failed",
            "Check your Spotify API credentials",
        )

    if status_code == 403:
        if "premium" in lowered:
            return (
                SpotifyMCPErrorCode.PREMIUM_REQUIRED,
                "Spotify Premium is required for this operation",
                "Upgrade to Spotify Premium to use playback features",
            )
        # `reason` is exact where matching the prose is a guess; spotipy also
        # prefixes the message with the request URL, which can contain "scope".
        if reason == "INSUFFICIENT_SCOPE" or "scope" in lowered:
            return (
                SpotifyMCPErrorCode.INSUFFICIENT_SCOPE,
                "Insufficient permissions for this operation",
                "Reauthorize Spotify to grant the required scopes, then restart "
                "the server. Do not delete the auth cache automatically",
            )
        return (
            SpotifyMCPErrorCode.PLAYBACK_RESTRICTED,
            "Playback is restricted for this content",
            None,
        )

    if status_code == 404:
        # Spotify's 404 prose is generic, so these mostly match spotipy's URL
        # prefix. Check playlists first: /playlists/{id}/tracks names both.
        if "playlist" in lowered:
            return (
                SpotifyMCPErrorCode.PLAYLIST_NOT_FOUND,
                "The requested playlist was not found",
                "Check the playlist ID and try again",
            )
        if "track" in lowered:
            return (
                SpotifyMCPErrorCode.TRACK_NOT_FOUND,
                "The requested track was not found",
                "Check the track ID and try again",
            )
        if "user" in lowered:
            return (
                SpotifyMCPErrorCode.USER_NOT_FOUND,
                "The requested user was not found",
                None,
            )

    if status_code == 429:
        retry_after = (getattr(exc, "headers", None) or {}).get("Retry-After")
        return (
            SpotifyMCPErrorCode.API_RATE_LIMITED,
            "Spotify API rate limit exceeded",
            f"Wait {retry_after}s before retrying"
            if retry_after
            else "Wait a moment before making more requests",
        )

    if status_code == 410:
        # A gone route is not a wrong ID or proof of universal API withdrawal.
        return (
            SpotifyMCPErrorCode.API_UNAVAILABLE,
            "Spotify no longer serves this capability for this app",
            "Run /spotify-api-watch to check for upstream changes",
        )

    if status_code and status_code >= 500:
        return (
            SpotifyMCPErrorCode.API_UNAVAILABLE,
            "Spotify API is temporarily unavailable",
            "Try again in a few minutes",
        )

    if "no active device" in lowered:
        return (
            SpotifyMCPErrorCode.NO_ACTIVE_DEVICE,
            "No active Spotify device found",
            "Open Spotify on a device to start playback",
        )

    if "device not found" in lowered:
        return (
            SpotifyMCPErrorCode.DEVICE_NOT_FOUND,
            "The specified device was not found",
            "Check available devices and try again",
        )

    return (
        SpotifyMCPErrorCode.UNKNOWN_ERROR,
        f"Spotify API error: {error_message}",
        None,
    )


def _format_error(error: SpotifyMCPError) -> str:
    """Build a model-facing message that keeps the suggestion and error code."""
    msg = error.message
    if error.suggestion:
        msg += f" — {error.suggestion}"
    if error.reason:
        msg += f" (spotify reason: {error.reason})"
    return f"{msg} [{error.code.value}]"


class ModelVisibleError(ToolError, ValueError):
    """A tool failure the model should read: bad arguments or a Spotify error.

    MCPServer forwards only a `ToolError`'s text. Any other exception reaches the
    model as a bare "Error executing tool <name>", which drops Spotify's reason.
    It stays a ValueError, which is how every tool reports these failures.
    """


def convert_spotify_error(e: SpotifyException) -> ModelVisibleError:
    """Convert a Spotify failure into an error whose message reaches the model."""
    return ModelVisibleError(_format_error(SpotifyMCPError.from_spotify_exception(e)))
