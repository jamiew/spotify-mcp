"""Spotify MCP server, built on the MCP Python SDK's MCPServer."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from importlib.metadata import version
from typing import TYPE_CHECKING, Annotated, cast

from mcp.server.mcpserver import (
    AcceptedElicitation,
    Context,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError
from mcp.types import Icon, ToolAnnotations
from pydantic import BaseModel
from spotipy import SpotifyException

import spotify_mcp.spotify_api as spotify_api
from spotify_mcp.errors import ModelVisibleError, convert_spotify_error
from spotify_mcp.logging_utils import log_tool_execution
from spotify_mcp.utils import to_id, to_playable_uri

if TYPE_CHECKING:
    from collections.abc import Callable

    from spotify_mcp.spotify_types import (
        AlbumObject,
        ArtistObject,
        AudiobookObject,
        EpisodeObject,
        PlaylistObject,
        ShowObject,
        TrackObject,
    )

logger = logging.getLogger(__name__)

# Guidance that applies to the whole surface lives here rather than in every tool
# description — it ships once per session instead of being repeated per tool.
INSTRUCTIONS = """\
Spotify for the signed-in user. Tracks, albums, artists, playlists, shows, episodes and
audiobooks are accepted as bare IDs or spotify: URIs anywhere.

Start from search_music to turn names into IDs. get_playlist_tracks returns zero-based
positions, which reorder_playlist and remove_tracks_from_playlist need.

Batch where you can: get_tracks and get_artist take up to 50 ids in one request,
get_album up to 20. Ask what the library already holds with check_saved_tracks (50),
check_saved_albums (20) or check_following_artists (50) instead of paging
get_saved_tracks. Quota is counted per developer account, so one batched request
beats fifty single ones. get_artist returns top tracks, and get_album its track
list, only when you ask for exactly one id.

Playback tools need Spotify Premium and an open device; if none is active, call
list_devices then transfer_playback. Play or queue podcast episodes with spotify:episode:
URIs (bare ids are tracks); play a whole show with its spotify:show: URI as context_uri.

Recommendations, audio features and related artists are unavailable to many apps;
this server does not expose those endpoints. Availability depends on app access,
not just the SDK. Spotify's AI-input policy applies even without model training.

New playlists are private unless public=true is explicitly requested. If Spotify
reports unexpected visibility, confirm it in the Spotify app.
Library writes accept 50 items (albums 20); membership checks (check_saved_*,
check_following_artists) accept 50 items or 20 albums. Requests use chunks of at most
40. Earlier writes may apply on failure. get_followed_artists pages by its `after`
cursor, not offset.
"""

# Spotify glyph (inline data URI). Only the server carries it: repeating it on every
# tool, resource and prompt made up over a quarter of the tools/list payload.
SPOTIFY_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PGNpcmNsZSBjeD0iMTIiIGN5PSIxMiIgcj0iMTIiIGZpbGw9IiMxREI5NTQiLz48cGF0aCBmaWxsPSIjZmZmIiBkPSJNMTcgMTYuNmEuNy43IDAgMCAxLTEgLjI1Yy0yLjctMS42NS02LjEtMi0xMC4xLTEuMWEuNzUuNzUgMCAxIDEtLjMzLTEuNDZjNC40LTEgOC4yLS42IDExLjIgMS4yNS4zNS4yLjQ2LjY2LjIzIDEuMDZ6bTEuMy0yLjk1YS45NC45NCAwIDAgMS0xLjI5LjNjLTMuMS0xLjktNy44LTIuNDYtMTEuNDUtMS4zNWEuOTQuOTQgMCAxIDEtLjU1LTEuOGM0LjE4LTEuMjcgOS4zNi0uNjUgMTIuOTMgMS41NS40NC4yNy41OC44NS4zNiAxLjN6bS4xLTMuMDdDMTQuNyA4LjQgOC45IDguMiA1LjQzIDkuMjZhMS4xMiAxLjEyIDAgMSAxLS42NS0yLjE1QzguNzYgNS45IDE1LjE4IDYuMTMgMTkuNDUgOC42NmExLjEyIDEuMTIgMCAxIDEtMS4xNSAxLjkyeiIvPjwvc3ZnPg==",
    mime_type="image/svg+xml",
)

mcp = MCPServer(
    "Spotify MCP",
    instructions=INSTRUCTIONS,
    website_url="https://github.com/jamiew/spotify-mcp",
    icons=[SPOTIFY_ICON],
    # v2 reports an empty serverInfo.version unless the server supplies one.
    version=version("spotify-mcp-jamiew"),
)

spotify_client = spotify_api.create_client()


# Data models for structured output
class Track(BaseModel):
    """A Spotify track with metadata."""

    name: str
    id: str | None = None
    artist: str
    artists: list[str] | None = None
    album: str | None = None
    album_id: str | None = None
    release_date: str | None = None
    duration_ms: int | None = None
    popularity: int | None = None
    external_urls: dict[str, str] | None = None
    added_at: str | None = None
    played_at: str | None = None
    is_local: bool = False
    # "episode" for podcast episodes, which have a show instead of artists/album.
    type: str | None = None
    show: str | None = None
    show_id: str | None = None


class PlaybackState(BaseModel):
    """Current playback state."""

    is_playing: bool
    track: Track | None = None
    device: str | None = None
    volume: int | None = None
    shuffle: bool = False
    repeat: str = "off"
    progress_ms: int | None = None
    # "track", "episode", "ad" or "unknown"; explains a playing state with no track.
    currently_playing_type: str | None = None


class Playlist(BaseModel):
    """A Spotify playlist."""

    name: str
    id: str
    owner: str | None = None
    description: str | None = None
    tracks: list[Track] | None = None
    total_tracks: int | None = None
    public: bool | None = None


class Artist(BaseModel):
    """A Spotify artist."""

    name: str
    id: str
    genres: list[str] | None = None
    popularity: int | None = None
    followers: int | None = None


class Album(BaseModel):
    """A Spotify album."""

    name: str
    id: str
    artist: str
    artists: list[str] | None = None
    release_date: str | None = None
    release_date_precision: str | None = None
    total_tracks: int | None = None
    album_type: str | None = None
    label: str | None = None
    genres: list[str] | None = None
    popularity: int | None = None
    external_urls: dict[str, str] | None = None
    added_at: str | None = None


# Tool result models (give every tool a real output schema)
class SearchResults(BaseModel):
    """Paginated search results."""

    items: list[Track]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class QueueState(BaseModel):
    """Currently playing track plus the upcoming queue."""

    currently_playing: Track | None = None
    queue: list[Track]


class TrackList(BaseModel):
    """A list of tracks."""

    tracks: list[Track]


class ArtistInfo(BaseModel):
    """One or more artists, with top tracks when a single artist was requested."""

    artists: list[Artist]
    # Only populated for a single-artist request: top tracks are per artist, and
    # Spotify withholds the endpoint from restricted apps entirely.
    top_tracks: list[Track]


class AlbumInfo(BaseModel):
    """One or more albums, with the track list when a single album was requested."""

    albums: list[Album]
    # Only populated for a single-album request, for the same reason.
    tracks: list[Track]


class MembershipStatus(BaseModel):
    """Whether each requested id is in the user's library, keyed by Spotify id."""

    # Dict rather than a parallel list so a client cannot mis-zip ids to answers.
    results: dict[str, bool]
    checked: int


class PlaylistList(BaseModel):
    """Paginated list of playlists."""

    items: list[Playlist]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class PlaylistTracks(BaseModel):
    """Paginated tracks from a single playlist."""

    items: list[Track]
    total: int
    limit: int | None = None
    offset: int
    returned: int


class SavedTracks(BaseModel):
    """Paginated saved/liked tracks."""

    items: list[Track]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class SavedAlbums(BaseModel):
    """Paginated saved albums."""

    items: list[Album]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class Show(BaseModel):
    """A podcast show. `publisher` is stripped for restricted apps."""

    name: str
    id: str
    publisher: str | None = None
    description: str | None = None
    total_episodes: int | None = None
    media_type: str | None = None
    explicit: bool | None = None
    external_urls: dict[str, str] | None = None
    added_at: str | None = None


class Episode(BaseModel):
    """A podcast episode, with the user's resume point where Spotify reports one."""

    name: str
    id: str | None = None
    show: str | None = None
    show_id: str | None = None
    description: str | None = None
    release_date: str | None = None
    duration_ms: int | None = None
    explicit: bool | None = None
    resume_position_ms: int | None = None
    fully_played: bool | None = None
    external_urls: dict[str, str] | None = None
    added_at: str | None = None


class SavedShows(BaseModel):
    """Paginated saved podcast shows."""

    items: list[Show]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class EpisodeList(BaseModel):
    """Paginated podcast episodes (a show's episodes, or the saved ones)."""

    items: list[Episode]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class Audiobook(BaseModel):
    """An audiobook. `publisher` is stripped for restricted apps."""

    name: str
    id: str
    authors: list[str] | None = None
    narrators: list[str] | None = None
    publisher: str | None = None
    description: str | None = None
    edition: str | None = None
    total_chapters: int | None = None
    explicit: bool | None = None
    external_urls: dict[str, str] | None = None
    added_at: str | None = None


class SavedAudiobooks(BaseModel):
    """Paginated saved audiobooks."""

    items: list[Audiobook]
    total: int
    limit: int
    offset: int
    next: str | None = None
    previous: str | None = None


class FollowedArtists(BaseModel):
    """A cursor page of followed artists.

    Spotify pages this list by cursor, not offset: pass `after` back to get the
    next page. It is None on the last page.
    """

    items: list[Artist]
    total: int | None = None
    limit: int
    after: str | None = None
    next: str | None = None


class Device(BaseModel):
    """A Spotify Connect device."""

    id: str | None = None
    name: str
    type: str | None = None
    is_active: bool = False
    volume_percent: int | None = None


class DeviceList(BaseModel):
    """The user's available devices."""

    devices: list[Device]


class UserProfile(BaseModel):
    """The signed-in user's profile.

    Everything but `id` is optional: Spotify's restricted regime strips these
    fields rather than erroring, so they must never be required here.
    """

    id: str
    display_name: str | None = None
    email: str | None = None
    country: str | None = None
    product: str | None = None
    followers: int | None = None


class TopItems(BaseModel):
    """The user's top artists or tracks over a time range."""

    time_range: str
    artists: list[Artist] | None = None
    tracks: list[Track] | None = None


class RecentlyPlayed(BaseModel):
    """Recently played tracks, most recent first."""

    items: list[Track]


class ActionResult(BaseModel):
    """Result of a state-changing operation."""

    status: str
    message: str
    snapshot_id: str | None = None


class RemovalConfirmation(BaseModel):
    """Elicitation schema confirming a destructive playlist edit."""

    confirm: bool = False


def parse_track(item: TrackObject) -> Track:
    """Parse Spotify track data into Track model.

    Tolerates entries with no ID (local files, unavailable/removed tracks):
    they are returned with `id=None` rather than dropped or raising.
    Podcast episodes come back with the show name in `artist` and `show`.
    """
    if item.get("type") == "episode":
        show = cast("dict[str, str]", item.get("show") or {})
        show_name = show.get("name")
        return Track(
            name=item.get("name") or "Unknown",
            id=item.get("id"),
            artist=show_name or "Unknown",
            release_date=item.get("release_date"),
            duration_ms=item.get("duration_ms"),
            external_urls=cast("dict[str, str]", item.get("external_urls")),
            type="episode",
            show=show_name,
            show_id=show.get("id"),
        )
    album_data = item.get("album", {})
    artists = item.get("artists", [])
    return Track(
        name=item.get("name") or "Unknown",
        id=item.get("id"),
        artist=artists[0]["name"] if artists else "Unknown",
        artists=[a["name"] for a in artists],
        album=album_data.get("name"),
        album_id=album_data.get("id"),
        release_date=album_data.get("release_date"),
        duration_ms=item.get("duration_ms"),
        popularity=item.get("popularity"),
        external_urls=cast("dict[str, str]", item.get("external_urls")),
        is_local=bool(item.get("is_local", False)),
        type=item.get("type"),
    )


def parse_album(item: AlbumObject) -> Album:
    """Parse a Spotify album object into the Album model."""
    artists = item.get("artists", [])
    return Album(
        name=item["name"],
        id=item["id"],
        artist=artists[0]["name"] if artists else "Unknown",
        artists=[a["name"] for a in artists],
        release_date=item.get("release_date"),
        release_date_precision=item.get("release_date_precision"),
        total_tracks=item.get("total_tracks"),
        album_type=item.get("album_type"),
        label=item.get("label"),
        genres=item.get("genres", []),
        popularity=item.get("popularity"),
        external_urls=cast("dict[str, str]", item.get("external_urls")),
    )


def parse_artist(item: ArtistObject) -> Artist:
    """Parse a Spotify artist object; restricted apps lose followers/popularity."""
    return Artist(
        name=item["name"],
        id=item["id"],
        genres=item.get("genres", []),
        popularity=item.get("popularity"),
        followers=(item.get("followers") or {}).get("total"),
    )


def parse_show(item: ShowObject) -> Show:
    """Parse a Spotify show object into the Show model."""
    return Show(
        name=item["name"],
        id=item["id"],
        publisher=item.get("publisher"),
        description=item.get("description"),
        total_episodes=item.get("total_episodes"),
        media_type=item.get("media_type"),
        explicit=item.get("explicit"),
        external_urls=cast("dict[str, str]", item.get("external_urls")),
    )


def parse_episode(item: EpisodeObject, show_id: str | None = None) -> Episode:
    """Parse an episode; a show's episode list carries no show, so pass its id."""
    show = cast("dict[str, str]", item.get("show") or {})
    resume = item.get("resume_point") or {}
    return Episode(
        name=item.get("name") or "Unknown",
        id=item.get("id"),
        show=show.get("name"),
        show_id=show.get("id") or show_id,
        description=item.get("description"),
        release_date=item.get("release_date"),
        duration_ms=item.get("duration_ms"),
        explicit=item.get("explicit"),
        resume_position_ms=resume.get("resume_position_ms"),
        fully_played=resume.get("fully_played"),
        external_urls=cast("dict[str, str]", item.get("external_urls")),
    )


def parse_audiobook(item: AudiobookObject) -> Audiobook:
    """Parse a Spotify audiobook object into the Audiobook model."""
    return Audiobook(
        name=item["name"],
        id=item["id"],
        authors=[a["name"] for a in item.get("authors", []) if a.get("name")],
        narrators=[n["name"] for n in item.get("narrators", []) if n.get("name")],
        publisher=item.get("publisher"),
        description=item.get("description"),
        edition=item.get("edition"),
        total_chapters=item.get("total_chapters"),
        explicit=item.get("explicit"),
        external_urls=cast("dict[str, str]", item.get("external_urls")),
    )


def extract_playlist_entry(row: object) -> TrackObject | None:
    """Pull the track object out of one playlist-items row.

    The Web API returns the entry under "item"; historically it was "track",
    which spotipy fixtures and older responses still use. Accept either, and
    ignore anything that is not an object (the nested "track" flag inside an
    entry is a bool, not a track).
    """
    if not isinstance(row, dict):
        return None
    for key in ("track", "item"):
        entry = row.get(key)
        if isinstance(entry, dict):
            return cast("TrackObject", entry)
    return None


def placeholder_track(row: dict[str, object]) -> Track:
    """Stand-in for a row whose entry is null (removed/region-locked track).

    Kept in the list so positions stay aligned with the playlist itself.
    """
    return Track(
        name="Unavailable",
        id=None,
        artist="Unknown",
        is_local=bool(row.get("is_local", False)),
    )


async def get_playlist_tracks_paginated(
    playlist_id: str,
    limit: int | None = None,
    offset: int = 0,
    ctx: Context | None = None,
    total: int | None = None,
) -> list[Track]:
    """Read playlist rows from `offset`, up to `limit` (None for all), 100 per request.

    Spotify calls run on a worker thread so the event loop stays free between
    progress notifications. `total` is only the progress denominator.
    """
    tracks: list[Track] = []
    current_offset = offset
    remaining = limit  # None means "every track"
    unavailable = 0

    while True:
        # Spotify API max is 100 per request
        batch_limit = 100 if remaining is None else min(100, remaining)
        if batch_limit <= 0:
            break

        tracks_result = await asyncio.to_thread(
            spotify_api.playlist_items,
            spotify_client,
            playlist_id,
            limit=batch_limit,
            offset=current_offset,
        )

        rows = (tracks_result or {}).get("items") or []
        if not rows:
            break

        for row in rows:
            entry = extract_playlist_entry(row)
            if entry is None:
                unavailable += 1
                tracks.append(placeholder_track(row if isinstance(row, dict) else {}))
                continue
            tracks.append(parse_track(entry))

        if ctx is not None:
            await ctx.report_progress(
                len(tracks), total, message=f"Fetched {len(tracks)} tracks so far"
            )

        # `limit`/`offset` count playlist positions, so consume the rows the API
        # returned - never the subset we managed to parse. Counting parsed
        # tracks lets an unparseable page hold `remaining` at its starting value
        # and page through the entire playlist regardless of `limit`.
        current_offset += len(rows)
        if remaining is not None:
            remaining -= len(rows)
            if remaining <= 0:
                break

        # A page can come back shorter than asked for without being the last one;
        # only a missing `next` means the end.
        if not tracks_result.get("next"):
            break

        # Safety check to prevent infinite loops
        if current_offset > 10000:
            logger.warning("Stopped reading playlist at offset %d", current_offset)
            break

    if unavailable:
        logger.warning(
            "%d playlist entries had no track object; returned placeholders",
            unavailable,
        )

    return tracks


def _check_batch(kind: str, ids: list[str]) -> None:
    """Enforce the kind's public per-call cap; upstream requests chunk at 40."""
    limit = spotify_api.BATCH_LIMITS[kind]
    if not ids:
        raise ValueError(f"At least one {kind} ID is required")
    if len(ids) > limit:
        raise ValueError(f"Maximum {limit} {kind} IDs per request, got {len(ids)}")


# === TOOLS ===


def _profile() -> UserProfile:
    """Read the signed-in user's profile into our model."""
    me = spotify_client.current_user() or {}
    return UserProfile(
        id=me["id"],
        display_name=me.get("display_name"),
        email=me.get("email"),
        country=me.get("country"),
        product=me.get("product"),
        followers=(me.get("followers") or {}).get("total"),
    )


@mcp.tool(
    title="Spotify Profile",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_me() -> UserProfile:
    """Get the signed-in user's Spotify profile.

    email, country and product come back empty on newer Spotify apps rather than
    erroring.
    """
    return _profile()


# Spotify's player writes are asynchronous: /me/player/play and friends answer 204
# immediately, but a GET straight afterwards can still serve the pre-action state, or
# None while an idle device wakes up (which reads back as is_playing=False with every
# field empty). Make a bounded, best-effort confirmation rather than trusting one read.
_CONFIRM_ATTEMPTS = 5
_CONFIRM_DELAY_S = 0.2


def _playback_confirmed(
    action: str,
    *,
    previous_track_id: str | None = None,
    position_ms: int | None = None,
    volume_percent: int | None = None,
    state: str | None = None,
) -> Callable[[PlaybackState], bool]:
    """Build the test for "this action has taken effect" for a given action.

    Anything without a meaningful test accepts the first read, preserving the old
    behaviour rather than spending attempts on a condition that can never be met.
    """
    if action == "play":
        # Only playing/not-playing is reliable here: with shuffle on, a context does
        # not necessarily start on its first track, so the track cannot be predicted.
        return lambda s: s.is_playing
    if action == "pause":
        return lambda s: not s.is_playing
    if action in ("next", "previous"):
        # A fresh track id, or any track at all if nothing was playing before.
        return lambda s: s.track is not None and s.track.id != previous_track_id
    if action == "volume":
        return lambda s: s.volume == volume_percent
    if action == "shuffle":
        return lambda s: s.shuffle is (state == "on")
    if action == "repeat":
        return lambda s: s.repeat == state
    if action == "seek" and position_ms is not None:
        # Playback keeps advancing, so accept a window ahead of the target rather
        # than an exact match, and reject a stale position from before the seek.
        return lambda s: (
            s.progress_ms is not None
            and position_ms - 500 <= s.progress_ms <= position_ms + 5000
        )
    return lambda _s: True


def _await_playback(matches: Callable[[PlaybackState], bool]) -> PlaybackState:
    """Read playback state until `matches` holds, within a bounded attempt count.

    Return the last observation if attempts run out or a later Spotify read fails.
    Confirmation failure does not mean the already-issued write failed.
    """
    state = _playback_state()
    for _ in range(_CONFIRM_ATTEMPTS - 1):
        if matches(state):
            return state
        time.sleep(_CONFIRM_DELAY_S)
        try:
            state = _playback_state()
        except SpotifyException as e:
            logger.warning(
                "Playback confirmation failed after a successful write; "
                "returning the last observation: %s",
                e,
            )
            return state
    return state


# Without additional_types the player reports a playing podcast episode as
# item=null, which reads as "playing, but nothing". Ask for both kinds.
PLAYABLE_TYPES = "track,episode"


def _playback_state() -> PlaybackState:
    """Read the current playback state into our model."""
    result = spotify_client.current_playback(additional_types=PLAYABLE_TYPES)
    device = (result or {}).get("device") or {}
    return PlaybackState(
        is_playing=result.get("is_playing", False) if result else False,
        track=parse_track(result["item"]) if result and result.get("item") else None,
        device=device.get("name"),
        volume=device.get("volume_percent"),
        shuffle=result.get("shuffle_state", False) if result else False,
        repeat=result.get("repeat_state", "off") if result else "off",
        progress_ms=result.get("progress_ms") if result else None,
        currently_playing_type=result.get("currently_playing_type") if result else None,
    )


@mcp.tool(
    title="Now Playing",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_playback_state() -> PlaybackState:
    """Get the current playback state: track, device, progress, shuffle and repeat."""
    return _playback_state()


@mcp.tool(
    title="Control Playback",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
@log_tool_execution
def control_playback(
    action: str,
    track_ids: list[str] | None = None,
    context_uri: str | None = None,
    position_ms: int | None = None,
    volume_percent: int | None = None,
    state: str | None = None,
    device_id: str | None = None,
) -> PlaybackState:
    """Control Spotify playback. Requires Premium and an active device.

    Args:
        action: 'play', 'pause', 'next', 'previous', 'seek', 'volume', 'shuffle' or 'repeat'
        track_ids: Tracks or episodes to play (action='play'; ignored when
            context_uri is set). Bare IDs are tracks; pass spotify:episode: URIs
            or episode share URLs for podcast episodes.
        context_uri: Album, playlist, artist or show URI to play (action='play')
        position_ms: Required for action='seek'
        volume_percent: 0-100, required for action='volume'
        state: 'on'/'off' for shuffle; 'track'/'context'/'off' for repeat
        device_id: Defaults to the active device

    Returns the last observed state. Confirmation is best effort: at most five
    post-action reads with brief waits. Stale state or a later read failure returns
    the last observation. Checks do not verify every requested track, context or
    device transition.
    """
    # Only the outgoing optional identity is needed; local tracks may have no
    # catalog id and must not be validated as a Track before issuing the action.
    previous_track_id: str | None = None
    if action in ("next", "previous"):
        before = spotify_client.current_playback(additional_types=PLAYABLE_TYPES)
        previous_track_id = ((before or {}).get("item") or {}).get("id")

    if action == "play":
        if context_uri:
            spotify_client.start_playback(device_id=device_id, context_uri=context_uri)
        elif track_ids:
            spotify_client.start_playback(
                device_id=device_id, uris=[to_playable_uri(t) for t in track_ids]
            )
        else:
            spotify_client.start_playback(device_id=device_id)
    elif action == "pause":
        spotify_client.pause_playback(device_id=device_id)
    elif action == "next":
        spotify_client.next_track(device_id=device_id)
    elif action == "previous":
        spotify_client.previous_track(device_id=device_id)
    elif action == "seek":
        if position_ms is None:
            raise ValueError("action='seek' requires position_ms")
        spotify_client.seek_track(position_ms, device_id=device_id)
    elif action == "volume":
        if volume_percent is None:
            raise ValueError("action='volume' requires volume_percent")
        spotify_client.volume(volume_percent, device_id=device_id)
    elif action == "shuffle":
        if state not in ("on", "off"):
            raise ValueError("action='shuffle' requires state='on' or 'off'")
        spotify_client.shuffle(state == "on", device_id=device_id)
    elif action == "repeat":
        if state not in ("track", "context", "off"):
            raise ValueError(
                "action='repeat' requires state='track', 'context' or 'off'"
            )
        spotify_client.repeat(state, device_id=device_id)
    else:
        raise ValueError(f"Invalid action: {action}")

    return _await_playback(
        _playback_confirmed(
            action,
            previous_track_id=previous_track_id,
            position_ms=position_ms,
            volume_percent=volume_percent,
            state=state,
        )
    )


@mcp.tool(
    title="Available Devices",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def list_devices() -> DeviceList:
    """List the user's available Spotify devices.

    Pass a device id to transfer_playback to make it active.
    """
    result = spotify_client.devices() or {}
    return DeviceList(
        devices=[
            Device(
                id=d.get("id"),
                name=d["name"],
                type=d.get("type"),
                is_active=d.get("is_active", False),
                volume_percent=d.get("volume_percent"),
            )
            for d in result.get("devices", [])
        ]
    )


@mcp.tool(
    title="Switch Device",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def transfer_playback(device_id: str, play: bool = True) -> ActionResult:
    """Move playback to a different device (see list_devices).

    play=False transfers without starting playback.
    """
    spotify_client.transfer_playback(device_id, force_play=play)
    return ActionResult(status="success", message="Playback transferred")


@mcp.tool(
    title="Search Spotify",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def search_music(
    query: str,
    qtype: str = "track",
    limit: int = 10,
    offset: int = 0,
    year: str | None = None,
    year_range: str | None = None,
    genre: str | None = None,
    artist: str | None = None,
    album: str | None = None,
) -> SearchResults:
    """Search Spotify for tracks, albums, artists, or playlists.

    Args:
        qtype: 'track', 'album', 'artist' or 'playlist'
        limit: Results per page, 1-50. Restricted apps are capped at 10 per page;
            a larger limit is retried at the cap. The returned `limit` is what was
            served; use `offset` to go deeper.
        year: A year ('2024'); year_range takes a span ('2020-2024')
        genre: Genre filter (e.g. 'electronic')

    year, year_range, genre, artist and album become Spotify search filters:
    query='love', year='2024', genre='pop' searches 'love year:2024 genre:pop'.
    """
    # Other types (show, episode, audiobook) come back in shapes this tool's
    # Track items can't describe, so refuse them instead of returning junk.
    if qtype not in ("track", "album", "artist", "playlist"):
        raise ValueError(
            f"qtype must be 'track', 'album', 'artist' or 'playlist', got {qtype!r}"
        )
    limit = max(1, min(spotify_api.SEARCH_LIMIT_MAX, limit))

    # Build filtered query
    filters = []
    if artist:
        filters.append(f"artist:{artist}")
    if album:
        filters.append(f"album:{album}")
    if year:
        filters.append(f"year:{year}")
    if year_range:
        filters.append(f"year:{year_range}")
    if genre:
        filters.append(f"genre:{genre}")

    full_query = " ".join([query] + filters) if filters else query

    result = spotify_api.search(
        spotify_client, full_query, qtype=qtype, limit=limit, offset=offset
    )

    tracks = []
    items_key = f"{qtype}s"
    result_section = result.get(items_key, {})
    # Spotify can return null entries in items (e.g. removed content), so guard each.
    if qtype == "track" and result_section.get("items"):
        tracks = [parse_track(item) for item in result_section["items"] if item]
    else:
        # Convert other types to track-like format for consistency
        if result_section.get("items"):
            for item in result_section["items"]:
                if not item:
                    continue
                track = Track(
                    name=item["name"],
                    id=item["id"],
                    artist=item.get("artists", [{}])[0].get("name", "Unknown")
                    if qtype != "artist"
                    else item["name"],
                    external_urls=item.get("external_urls"),
                )
                tracks.append(track)

    total_results = result_section.get("total", 0)

    return SearchResults(
        items=tracks,
        total=total_results,
        limit=result_section.get("limit", limit),
        offset=result_section.get("offset", offset),
        next=result_section.get("next"),
        previous=result_section.get("previous"),
    )


@mcp.tool(
    title="Add to Queue",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
@log_tool_execution
def add_to_queue(track_id: str) -> ActionResult:
    """Add a track or podcast episode to the playback queue.

    Args:
        track_id: Track ID, or a spotify:track:/spotify:episode: URI or
            open.spotify.com URL. A bare ID is taken as a track.
    """
    # Not string formatting: a URI or share URL would otherwise be pasted
    # into `spotify:track:` a second time, and an episode would become a track.
    uri = to_playable_uri(track_id)
    spotify_client.add_to_queue(uri)
    kind = uri.split(":")[1]
    return ActionResult(status="success", message=f"Added {kind} to queue")


@mcp.tool(
    title="Get Queue",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_queue() -> QueueState:
    """Get the currently playing item and the upcoming queue."""
    result = spotify_client.queue()

    queue_tracks = []
    if result.get("queue"):
        queue_tracks = [parse_track(item) for item in result["queue"]]

    return QueueState(
        currently_playing=parse_track(result["currently_playing"])
        if result.get("currently_playing")
        else None,
        queue=queue_tracks,
    )


@mcp.tool(
    title="Get Tracks",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_tracks(track_ids: str | list[str]) -> TrackList:
    """Get one or more Spotify tracks (up to 50 IDs) in one request.

    Where Spotify withholds the batch endpoint from an app, this falls back to one
    request per track with the same result.
    """
    # Normalize to list
    ids = [track_ids] if isinstance(track_ids, str) else track_ids

    if len(ids) > 50:
        raise ValueError("Maximum 50 track IDs per request (Spotify API limit)")

    tracks = [
        parse_track(cast("TrackObject", item))
        for item in spotify_api.get_tracks(spotify_client, ids)
        if item
    ]

    return TrackList(tracks=tracks)


# /artists/{id}/top-tracks is withheld from restricted apps: it answers 403
# rather than the 400/404 that `with_fallback` treats as a regime miss, and no
# alternative path serves it. Degrade to no top tracks instead of failing the
# whole tool, the same way the stripped `followers`/`popularity` fields do.


def _artist_top_tracks_or_empty(artist_id: str) -> dict:
    """Read an artist's top tracks, returning `{}` when Spotify withholds them."""
    try:
        top_tracks: dict = spotify_client.artist_top_tracks(artist_id)
        return top_tracks
    except SpotifyException as e:
        if e.http_status != 403:
            raise
        return {}


@mcp.tool(
    title="Get Artist",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_artist(artist_ids: str | list[str]) -> ArtistInfo:
    """Get details for one or more Spotify artists, batched into a single request.

    Args:
        artist_ids: One artist ID/URI, or a list of up to 50

    `artists` follows the requested order. `top_tracks` is filled only when a single
    artist was requested, and is empty rather than an error when Spotify withholds
    that endpoint.
    """
    ids = [artist_ids] if isinstance(artist_ids, str) else list(artist_ids)
    _check_batch("artist", ids)

    results = cast("list[ArtistObject]", spotify_api.get_artists(spotify_client, ids))
    artists = [parse_artist(result) for result in results]

    tracks: list[Track] = []
    if len(ids) == 1 and artists:
        top_tracks = _artist_top_tracks_or_empty(artists[0].id)
        tracks = [parse_track(t) for t in top_tracks.get("tracks", [])[:10]]

    return ArtistInfo(artists=artists, top_tracks=tracks)


def _total_tracks(playlist_id: str, reported: object) -> int | None:
    """Prefer the count Spotify reported, else read it off the items endpoint."""
    if isinstance(reported, int):
        return reported
    try:
        return spotify_api.playlist_total(spotify_client, playlist_id)
    except SpotifyException as e:
        # Restricted apps can read metadata without access to playlist contents.
        if e.http_status != 403:
            raise
        return None


def _playlist_metadata(playlist_id: str) -> Playlist:
    """Read a playlist's metadata, without its tracks."""
    result: PlaylistObject = spotify_client.playlist(
        playlist_id, fields="id,name,description,owner,public,tracks.total"
    )
    owner = result.get("owner") or {}
    tracks = result.get("tracks") or {}
    return Playlist(
        name=result["name"],
        id=result["id"],
        owner=owner.get("display_name"),
        description=result.get("description"),
        total_tracks=_total_tracks(playlist_id, tracks.get("total")),
        public=result.get("public"),
    )


@mcp.tool(
    title="Get Playlist",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_playlist(playlist_id: str) -> Playlist:
    """Get a playlist's metadata and total_tracks; get_playlist_tracks reads tracks."""
    return _playlist_metadata(playlist_id)


@mcp.tool(
    title="Create Playlist",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
@log_tool_execution
def create_playlist(name: str, description: str = "", public: bool = False) -> Playlist:
    """Create a new Spotify playlist. It is private unless public=True."""
    result = spotify_api.create_playlist(spotify_client, name, description, public)

    playlist = Playlist(
        name=result["name"],
        id=result["id"],
        owner=result.get("owner", {}).get("display_name"),
        description=result.get("description"),
        tracks=[],
        total_tracks=0,
        public=result.get("public"),
    )

    return playlist


@mcp.tool(
    title="Add Tracks to Playlist",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
@log_tool_execution
def add_tracks_to_playlist(playlist_id: str, track_uris: list[str]) -> ActionResult:
    """Add tracks or podcast episodes to a playlist.

    Args:
        track_uris: Track or episode URIs/URLs (up to 100). Bare IDs are tracks;
            pass spotify:episode: URIs or episode URLs for episodes.
    """
    uris = [to_playable_uri(uri) for uri in track_uris]

    result = spotify_api.playlist_add_items(spotify_client, playlist_id, uris)
    return ActionResult(
        status="success",
        message=f"Added {len(uris)} tracks to playlist",
        snapshot_id=result.get("snapshot_id") if result else None,
    )


@mcp.tool(
    title="List My Playlists",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def list_playlists(limit: int = 20, offset: int = 0) -> PlaylistList:
    """Get the current user's playlists.

    limit is 1-50 per page; page with offset.
    """
    # Validate limit (Spotify API accepts 1-50)
    limit = max(1, min(50, limit))

    result = spotify_client.current_user_playlists(limit=limit, offset=offset)

    # Log pagination info

    playlists = []
    for item in result.get("items", []):
        item_typed = cast("PlaylistObject", item)
        owner = item_typed.get("owner") or {}
        tracks = item_typed.get("tracks") or {}
        playlist = Playlist(
            name=item_typed["name"],
            id=item_typed["id"],
            owner=owner.get("display_name"),
            description=item_typed.get("description"),
            total_tracks=tracks.get("total"),
            public=item_typed.get("public"),
        )
        playlists.append(playlist)

    return PlaylistList(
        items=playlists,
        total=result.get("total", 0),
        limit=result.get("limit", limit),
        offset=result.get("offset", offset),
        next=result.get("next"),
        previous=result.get("previous"),
    )


@mcp.tool(
    title="Get Playlist Tracks",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
async def get_playlist_tracks(
    playlist_id: str,
    limit: int | None = None,
    offset: int = 0,
    ctx: Context | None = None,
) -> PlaylistTracks:
    """Get tracks from a playlist with full pagination support.

    Args:
        playlist_id: Playlist ID
        limit: Max tracks to return (None for all tracks, up to 10,000 safety limit)
        offset: Number of tracks to skip for pagination (default 0)

    Returns:
        PlaylistTracks with 'items' (list of tracks), 'total', 'limit', 'offset'

    Note: Large playlists require pagination. Use limit/offset to get specific ranges:
    - Get first 100: limit=100, offset=0
    - Get next 100: limit=100, offset=100
    - Get all tracks: limit=None (use with caution on very large playlists)
    """
    # Metadata first: it survives a restricted regime that forbids contents,
    # and _total_tracks falls back to the items cursor when it is stripped.
    # Spotify calls run on a worker thread so they don't block the event loop.
    playlist_info = await asyncio.to_thread(
        spotify_client.playlist, playlist_id, fields="tracks.total"
    )
    total_tracks = await asyncio.to_thread(
        _total_tracks, playlist_id, (playlist_info.get("tracks") or {}).get("total")
    )

    tracks = await get_playlist_tracks_paginated(
        playlist_id, limit, offset, ctx=ctx, total=total_tracks
    )
    return PlaylistTracks(
        items=tracks,
        total=len(tracks) if total_tracks is None else total_tracks,
        limit=limit,
        offset=offset,
        returned=len(tracks),
    )


def _client_can_elicit_form(ctx: Context) -> bool:
    """Whether the client declared form elicitation.

    Mirrors the SDK's resolver gate: a bare `elicitation: {}` predates modes and
    means form support; a url-only declaration does not.
    """
    caps = ctx.client_capabilities
    elicitation = caps.elicitation if caps is not None else None
    return elicitation is not None and (
        elicitation.form is not None or elicitation.url is None
    )


def confirm_removal(
    playlist_id: str, track_uris: list[str], ctx: Context
) -> RemovalConfirmation | Elicit[RemovalConfirmation]:
    """Resolver that asks the user before tracks leave a playlist.

    Clients that can't render a form proceed unconfirmed, so this core op never
    hard-fails on them. When they can, a failed prompt fails the call rather than
    deleting. On 2026-07-28 connections the call is retried with the answer
    attached, so the question must depend only on the arguments.
    """
    # Reject malformed input before bothering the user about it. Resolvers sit
    # outside log_tool_execution, so surface the reason here.
    try:
        uris = [to_playable_uri(uri) for uri in track_uris]
    except ValueError as e:
        raise ModelVisibleError(str(e)) from e
    if not _client_can_elicit_form(ctx):
        return RemovalConfirmation(confirm=True)
    return Elicit(
        f"Remove {len(uris)} track(s) from playlist {playlist_id}? "
        "This cannot be undone.",
        RemovalConfirmation,
    )


@mcp.tool(
    title="Remove Tracks from Playlist",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
@log_tool_execution
def remove_tracks_from_playlist(
    playlist_id: str,
    track_uris: list[str],
    confirmation: Annotated[
        ElicitationResult[RemovalConfirmation], Resolve(confirm_removal)
    ],
) -> ActionResult:
    """Remove tracks or podcast episodes from a playlist.

    Args:
        track_uris: Track or episode URIs/URLs to remove. Bare IDs are tracks.
    """
    uris = [to_playable_uri(uri) for uri in track_uris]

    # Decline, cancel, or accepting with the box unticked all keep the tracks.
    if not (
        isinstance(confirmation, AcceptedElicitation) and confirmation.data.confirm
    ):
        return ActionResult(
            status="cancelled",
            message="Removal cancelled by user",
        )

    result = spotify_api.playlist_remove_items(spotify_client, playlist_id, uris)
    return ActionResult(
        status="success",
        message=f"Removed {len(uris)} tracks from playlist",
        snapshot_id=result.get("snapshot_id") if result else None,
    )


@mcp.tool(
    title="Edit Playlist Details",
    annotations=ToolAnnotations(
        read_only_hint=False,
        # Overwrites existing metadata, so clients should confirm it.
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def update_playlist_details(
    playlist_id: str,
    name: str | None = None,
    description: str | None = None,
    public: bool | None = None,
) -> ActionResult:
    """Change a playlist's name, description or visibility; omitted fields stay."""
    if not name and not description and public is None:
        raise ValueError(
            "At least one of name, description, or public must be provided"
        )

    spotify_client.playlist_change_details(
        playlist_id, name=name, description=description, public=public
    )
    return ActionResult(
        status="success", message="Playlist details updated successfully"
    )


@mcp.tool(
    title="Reorder Playlist",
    annotations=ToolAnnotations(
        read_only_hint=False,
        # Rewrites existing track order in place, so clients should confirm it.
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
@log_tool_execution
def reorder_playlist(
    playlist_id: str,
    range_start: int,
    insert_before: int,
    range_length: int = 1,
    snapshot_id: str | None = None,
) -> ActionResult:
    """Move a contiguous block of tracks to a new position within a playlist.

    Args:
        range_start: Zero-based position of the first track to move
        insert_before: Zero-based position to insert the moved block before.
            Pass the playlist's total track count to move the block to the end.
        range_length: Number of consecutive tracks to move
        snapshot_id: Optional playlist snapshot ID to guard against concurrent edits

    Returns the new snapshot_id. Example: move the first 3 tracks to just before
    position 10 with range_start=0, range_length=3, insert_before=10.
    """
    if range_start < 0 or insert_before < 0:
        raise ValueError("range_start and insert_before must be >= 0")
    if range_length < 1:
        raise ValueError("range_length must be >= 1")

    result = spotify_api.playlist_reorder_items(
        spotify_client,
        playlist_id,
        range_start=range_start,
        insert_before=insert_before,
        range_length=range_length,
        snapshot_id=snapshot_id,
    )
    return ActionResult(
        status="success",
        message=(
            f"Moved {range_length} track(s) from position {range_start} "
            f"to before position {insert_before}"
        ),
        snapshot_id=result.get("snapshot_id") if result else None,
    )


@mcp.tool(
    title="Get Album",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_album(album_ids: str | list[str]) -> AlbumInfo:
    """Get details for one or more Spotify albums, batched into a single request.

    Args:
        album_ids: One album ID/URI, or a list of up to 20 (Spotify's album cap)

    `albums` follows the requested order. `tracks` holds the album's track list only
    when a single album was requested.
    """
    ids = [album_ids] if isinstance(album_ids, str) else list(album_ids)
    _check_batch("album", ids)

    results = cast("list[AlbumObject]", spotify_api.get_albums(spotify_client, ids))

    albums = [parse_album(result) for result in results]

    # Album track items carry no album of their own, so stamp the parent on.
    tracks = []
    if len(ids) == 1 and results:
        parent = results[0]
        # Typed as list[dict] so the empty-row guard stays reachable: Spotify
        # sends null items, which the TypedDict shape does not admit.
        items = cast("list[dict]", (parent.get("tracks") or {}).get("items") or [])
        for item in items:
            if not item:
                continue
            item["album"] = {
                "name": parent["name"],
                "id": parent["id"],
                "release_date": parent.get("release_date"),
            }
            tracks.append(parse_track(cast("TrackObject", item)))

    return AlbumInfo(albums=albums, tracks=tracks)


@mcp.tool(
    title="Get Liked Songs",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_saved_tracks(limit: int = 20, offset: int = 0) -> SavedTracks:
    """Get the user's Liked Songs, with added_at.

    limit is 1-50 per page; page with offset.
    """
    limit = max(1, min(50, limit))

    result = spotify_client.current_user_saved_tracks(limit=limit, offset=offset)

    tracks = []
    for item in result.get("items", []):
        if item and item.get("track"):
            track = parse_track(item["track"])
            track.added_at = item.get("added_at")
            tracks.append(track)

    return SavedTracks(
        items=tracks,
        total=result.get("total", 0),
        limit=result.get("limit", limit),
        offset=result.get("offset", offset),
        next=result.get("next"),
        previous=result.get("previous"),
    )


def _library_write(
    kind: str, ids: list[str], write: Callable[[list[str]], None], done: str
) -> ActionResult:
    """Validate a library write, run it, report it."""
    _check_batch(kind, ids)
    write(ids)
    return ActionResult(status="success", message=f"{done} {len(ids)} {kind}(s)")


@mcp.tool(
    title="Like Tracks",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def save_tracks(track_ids: list[str]) -> ActionResult:
    """Save (like) tracks to the user's library.

    Args:
        track_ids: Track IDs or URIs (up to 50)
    """
    return _library_write(
        "track",
        track_ids,
        lambda ids: spotify_api.save_tracks(spotify_client, ids),
        "Saved",
    )


@mcp.tool(
    title="Unlike Tracks",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def remove_saved_tracks(track_ids: list[str]) -> ActionResult:
    """Remove tracks from the user's saved (liked) tracks.

    Args:
        track_ids: Track IDs or URIs (up to 50)
    """
    return _library_write(
        "track",
        track_ids,
        lambda ids: spotify_api.remove_saved_tracks(spotify_client, ids),
        "Removed",
    )


def _membership(
    kind: str, ids: list[str], read: Callable[[list[str]], list[bool]]
) -> MembershipStatus:
    """Ask Spotify which ids are already saved or followed in bounded requests."""
    _check_batch(kind, ids)

    flags = read(ids)
    # Spotify answers positionally; key by the id it answered for so the caller
    # cannot mis-zip the two lists.
    return MembershipStatus(
        results={to_id(i): bool(flag) for i, flag in zip(ids, flags, strict=True)},
        checked=len(ids),
    )


@mcp.tool(
    title="Check Liked Songs",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def check_saved_tracks(track_ids: list[str]) -> MembershipStatus:
    """Check which tracks are already liked, without paging the whole library.

    Up to 50 tracks per tool call, split into upstream chunks of at most 40.
    Use this before save_tracks rather than paging through get_saved_tracks.

    Args:
        track_ids: Track IDs or URIs (up to 50)
    Returns:
        MembershipStatus.results maps each Spotify track id to true if liked
    """
    return _membership(
        "track",
        track_ids,
        lambda ids: spotify_api.saved_tracks_contains(spotify_client, ids),
    )


@mcp.tool(
    title="Check Saved Albums",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def check_saved_albums(album_ids: list[str]) -> MembershipStatus:
    """Check which albums are already saved to the library.

    Up to 20 albums per tool call, preserving this tool's existing album cap.

    Args:
        album_ids: Album IDs or URIs (up to 20)
    Returns:
        MembershipStatus.results maps each Spotify album id to true if saved
    """
    return _membership(
        "album",
        album_ids,
        lambda ids: spotify_api.saved_albums_contains(spotify_client, ids),
    )


@mcp.tool(
    title="Check Followed Artists",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def check_following_artists(artist_ids: list[str]) -> MembershipStatus:
    """Check up to 50 artist follows in upstream chunks of at most 40.

    Uses artist URIs on the consolidated library route, with a legacy fallback.
    Requires user-follow-read; reauthorize if your existing grant lacks it.

    Args:
        artist_ids: Artist IDs or URIs (up to 50)
    Returns:
        MembershipStatus.results maps each Spotify artist id to true if followed
    """
    return _membership(
        "artist",
        artist_ids,
        lambda ids: spotify_api.following_artists_contains(spotify_client, ids),
    )


@mcp.tool(
    title="Get Saved Albums",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_saved_albums(limit: int = 20, offset: int = 0) -> SavedAlbums:
    """Get albums saved in the user's library, with added_at.

    limit is 1-50 per page; page with offset.
    """
    limit = max(1, min(50, limit))

    result = spotify_client.current_user_saved_albums(limit=limit, offset=offset) or {}

    albums = []
    for item in result.get("items", []):
        if item and item.get("album"):
            album = parse_album(item["album"])
            album.added_at = item.get("added_at")
            albums.append(album)

    return SavedAlbums(
        items=albums,
        total=result.get("total", 0),
        limit=result.get("limit", limit),
        offset=result.get("offset", offset),
        next=result.get("next"),
        previous=result.get("previous"),
    )


@mcp.tool(
    title="Save Albums",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def save_albums(album_ids: list[str]) -> ActionResult:
    """Save albums to the user's library.

    Args:
        album_ids: Album IDs or URIs (up to 20)
    """
    return _library_write(
        "album",
        album_ids,
        lambda ids: spotify_api.save_albums(spotify_client, ids),
        "Saved",
    )


@mcp.tool(
    title="Remove Saved Albums",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def remove_saved_albums(album_ids: list[str]) -> ActionResult:
    """Remove albums from the user's library.

    Args:
        album_ids: Album IDs or URIs (up to 20)
    """
    return _library_write(
        "album",
        album_ids,
        lambda ids: spotify_api.remove_saved_albums(spotify_client, ids),
        "Removed",
    )


@mcp.tool(
    title="Get Followed Artists",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_followed_artists(limit: int = 20, after: str | None = None) -> FollowedArtists:
    """Get the artists the user follows, 1-50 per page (limit).

    Paged by cursor, not offset: pass the returned `after` back for the next page.
    It is null on the last page.
    """
    limit = max(1, min(50, limit))

    result = spotify_client.current_user_followed_artists(
        limit=limit, after=to_id(after) if after else None
    )
    page = (result or {}).get("artists") or {}

    return FollowedArtists(
        items=[parse_artist(a) for a in page.get("items", []) if a],
        total=page.get("total"),
        limit=page.get("limit", limit),
        after=(page.get("cursors") or {}).get("after"),
        next=page.get("next"),
    )


@mcp.tool(
    title="Follow Artists",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def follow_artists(artist_ids: list[str]) -> ActionResult:
    """Follow artists. Requires user-follow-modify; reauthorize if it is missing.

    Args:
        artist_ids: Artist IDs or URIs (up to 50)
    """
    return _library_write(
        "artist",
        artist_ids,
        lambda ids: spotify_api.follow_artists(spotify_client, ids),
        "Followed",
    )


@mcp.tool(
    title="Unfollow Artists",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def unfollow_artists(artist_ids: list[str]) -> ActionResult:
    """Unfollow artists. Requires user-follow-modify; reauthorize if it is missing.

    Args:
        artist_ids: Artist IDs or URIs (up to 50)
    """
    return _library_write(
        "artist",
        artist_ids,
        lambda ids: spotify_api.unfollow_artists(spotify_client, ids),
        "Unfollowed",
    )


@mcp.tool(
    title="Follow Playlist",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def follow_playlist(playlist_id: str) -> ActionResult:
    """Follow (save) a playlist so it appears in the user's library.

    Args:
        playlist_id: Playlist ID, URI or open.spotify.com URL
    """
    spotify_api.follow_playlist(spotify_client, playlist_id)
    return ActionResult(status="success", message="Playlist followed")


@mcp.tool(
    title="Unfollow Playlist",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def unfollow_playlist(playlist_id: str) -> ActionResult:
    """Unfollow a playlist, removing it from the user's library.

    For playlists the user owns this is how Spotify deletes them — there is no
    separate delete endpoint.

    Args:
        playlist_id: Playlist ID or URI
    """
    spotify_api.unfollow_playlist(spotify_client, playlist_id)
    return ActionResult(status="success", message="Playlist unfollowed/deleted")


@mcp.tool(
    title="Get Saved Shows",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_saved_shows(limit: int = 20, offset: int = 0) -> SavedShows:
    """Get podcast shows saved in the user's library.

    limit is 1-50 per page; page with offset.
    """
    limit = max(1, min(50, limit))

    result = spotify_client.current_user_saved_shows(limit=limit, offset=offset) or {}

    shows = []
    for item in result.get("items", []):
        if item and item.get("show"):
            show = parse_show(item["show"])
            show.added_at = item.get("added_at")
            shows.append(show)

    return SavedShows(
        items=shows,
        total=result.get("total", 0),
        limit=result.get("limit", limit),
        offset=result.get("offset", offset),
        next=result.get("next"),
        previous=result.get("previous"),
    )


@mcp.tool(
    title="Save Shows",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def save_shows(show_ids: list[str]) -> ActionResult:
    """Save (follow) podcast shows to the user's library.

    Args:
        show_ids: Show IDs or URIs (up to 50)
    """
    return _library_write(
        "show",
        show_ids,
        lambda ids: spotify_api.save_shows(spotify_client, ids),
        "Saved",
    )


@mcp.tool(
    title="Remove Saved Shows",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def remove_saved_shows(show_ids: list[str]) -> ActionResult:
    """Remove podcast shows from the user's library.

    Args:
        show_ids: Show IDs or URIs (up to 50)
    """
    return _library_write(
        "show",
        show_ids,
        lambda ids: spotify_api.remove_saved_shows(spotify_client, ids),
        "Removed",
    )


@mcp.tool(
    title="Check Saved Shows",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def check_saved_shows(show_ids: list[str]) -> MembershipStatus:
    """Check which podcast shows are already saved, in upstream chunks of 40.

    Args:
        show_ids: Show IDs or URIs (up to 50)
    Returns:
        MembershipStatus.results maps each Spotify show id to true if saved
    """
    return _membership(
        "show",
        show_ids,
        lambda ids: spotify_api.saved_shows_contains(spotify_client, ids),
    )


@mcp.tool(
    title="Check Saved Episodes",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def check_saved_episodes(episode_ids: list[str]) -> MembershipStatus:
    """Check which podcast episodes are already saved, in upstream chunks of 40.

    Args:
        episode_ids: Episode IDs or URIs (up to 50)
    Returns:
        MembershipStatus.results maps each Spotify episode id to true if saved
    """
    return _membership(
        "episode",
        episode_ids,
        lambda ids: spotify_api.saved_episodes_contains(spotify_client, ids),
    )


@mcp.tool(
    title="Get Show Episodes",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_show_episodes(show_id: str, limit: int = 20, offset: int = 0) -> EpisodeList:
    """Get a podcast show's episodes, with the user's resume point for each.

    limit is 1-50 per page; page with offset.
    """
    limit = max(1, min(50, limit))
    sid = to_id(show_id)

    result = spotify_client.show_episodes(sid, limit=limit, offset=offset) or {}

    episodes = [
        parse_episode(item, show_id=sid) for item in result.get("items", []) if item
    ]

    return EpisodeList(
        items=episodes,
        total=result.get("total", 0),
        limit=result.get("limit", limit),
        offset=result.get("offset", offset),
        next=result.get("next"),
        previous=result.get("previous"),
    )


@mcp.tool(
    title="Get Saved Episodes",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_saved_episodes(limit: int = 20, offset: int = 0) -> EpisodeList:
    """Get podcast episodes saved in the user's library.

    limit is 1-50 per page; page with offset.
    """
    limit = max(1, min(50, limit))

    result = (
        spotify_client.current_user_saved_episodes(limit=limit, offset=offset) or {}
    )

    episodes = []
    for item in result.get("items", []):
        if item and item.get("episode"):
            episode = parse_episode(item["episode"])
            episode.added_at = item.get("added_at")
            episodes.append(episode)

    return EpisodeList(
        items=episodes,
        total=result.get("total", 0),
        limit=result.get("limit", limit),
        offset=result.get("offset", offset),
        next=result.get("next"),
        previous=result.get("previous"),
    )


@mcp.tool(
    title="Save Episodes",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def save_episodes(episode_ids: list[str]) -> ActionResult:
    """Save podcast episodes to the user's library.

    Args:
        episode_ids: Episode IDs or URIs (up to 50)
    """
    return _library_write(
        "episode",
        episode_ids,
        lambda ids: spotify_api.save_episodes(spotify_client, ids),
        "Saved",
    )


@mcp.tool(
    title="Remove Saved Episodes",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def remove_saved_episodes(episode_ids: list[str]) -> ActionResult:
    """Remove podcast episodes from the user's library.

    Args:
        episode_ids: Episode IDs or URIs (up to 50)
    """
    return _library_write(
        "episode",
        episode_ids,
        lambda ids: spotify_api.remove_saved_episodes(spotify_client, ids),
        "Removed",
    )


@mcp.tool(
    title="Get Saved Audiobooks",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_saved_audiobooks(limit: int = 20, offset: int = 0) -> SavedAudiobooks:
    """Get audiobooks saved in the user's library.

    limit is 1-50 per page; page with offset.
    """
    limit = max(1, min(50, limit))

    result = spotify_client._get("me/audiobooks", limit=limit, offset=offset) or {}

    books = []
    for item in result.get("items", []):
        if not item:
            continue
        # The reference documents bare audiobook objects here, while the other
        # saved-item lists wrap theirs with added_at. Accept either shape.
        if isinstance(item.get("audiobook"), dict):
            book = parse_audiobook(item["audiobook"])
            book.added_at = item.get("added_at")
        elif item.get("id"):
            book = parse_audiobook(item)
        else:
            continue
        books.append(book)

    return SavedAudiobooks(
        items=books,
        total=result.get("total", 0),
        limit=result.get("limit", limit),
        offset=result.get("offset", offset),
        next=result.get("next"),
        previous=result.get("previous"),
    )


@mcp.tool(
    title="Check Saved Audiobooks",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def check_saved_audiobooks(audiobook_ids: list[str]) -> MembershipStatus:
    """Check which audiobooks are already saved, in upstream chunks of 40.

    Args:
        audiobook_ids: Audiobook IDs or URIs (up to 50)
    Returns:
        MembershipStatus.results maps each Spotify audiobook id to true if saved
    """
    return _membership(
        "audiobook",
        audiobook_ids,
        lambda ids: spotify_api.saved_audiobooks_contains(spotify_client, ids),
    )


@mcp.tool(
    title="Save Audiobooks",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def save_audiobooks(audiobook_ids: list[str]) -> ActionResult:
    """Save audiobooks to the user's library.

    Args:
        audiobook_ids: Audiobook IDs or URIs (up to 50)
    """
    return _library_write(
        "audiobook",
        audiobook_ids,
        lambda ids: spotify_api.save_audiobooks(spotify_client, ids),
        "Saved",
    )


@mcp.tool(
    title="Remove Saved Audiobooks",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
@log_tool_execution
def remove_saved_audiobooks(audiobook_ids: list[str]) -> ActionResult:
    """Remove audiobooks from the user's library.

    Args:
        audiobook_ids: Audiobook IDs or URIs (up to 50)
    """
    return _library_write(
        "audiobook",
        audiobook_ids,
        lambda ids: spotify_api.remove_saved_audiobooks(spotify_client, ids),
        "Removed",
    )


@mcp.tool(
    title="Recently Played",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_recently_played(limit: int = 20) -> RecentlyPlayed:
    """Get recently played tracks with played_at, most recent first (limit 1-50)."""
    limit = max(1, min(50, limit))

    result = spotify_client.current_user_recently_played(limit=limit) or {}

    tracks = []
    for item in result.get("items", []):
        if item and item.get("track"):
            track = parse_track(item["track"])
            track.played_at = item.get("played_at")
            tracks.append(track)

    return RecentlyPlayed(items=tracks)


@mcp.tool(
    title="Top Artists and Tracks",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=True
    ),
)
@log_tool_execution
def get_top_items(
    item_type: str = "tracks", time_range: str = "medium_term", limit: int = 20
) -> TopItems:
    """Get the user's top artists or tracks over a time range.

    This remains useful when recommendations or related artists are unavailable
    to the app. Spotify's policy also restricts analysis and AI ingestion.

    Args:
        item_type: 'tracks' or 'artists'
        time_range: 'short_term' (~4 weeks), 'medium_term' (~6 months) or 'long_term'
        limit: 1-50
    """
    if item_type not in ("tracks", "artists"):
        raise ValueError("item_type must be 'tracks' or 'artists'")
    if time_range not in ("short_term", "medium_term", "long_term"):
        raise ValueError(
            "time_range must be 'short_term', 'medium_term' or 'long_term'"
        )
    limit = max(1, min(50, limit))

    if item_type == "tracks":
        result = spotify_client.current_user_top_tracks(
            limit=limit, time_range=time_range
        )
        return TopItems(
            time_range=time_range,
            tracks=[parse_track(t) for t in (result or {}).get("items", []) if t],
        )

    result = spotify_client.current_user_top_artists(limit=limit, time_range=time_range)
    return TopItems(
        time_range=time_range,
        artists=[parse_artist(a) for a in (result or {}).get("items", []) if a],
    )


# === RESOURCES ===


def spotify_resource[F: Callable[..., BaseModel]](func: F) -> F:
    """Report a Spotify failure as a resource read error instead of content.

    MCPServer forwards a `ResourceError`'s message to the client and hides the
    text of anything else. A 404 becomes `ResourceNotFoundError`.
    """

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> BaseModel:
        try:
            return func(*args, **kwargs)
        except SpotifyException as e:
            message = str(convert_spotify_error(e))
            if e.http_status == 404:
                raise ResourceNotFoundError(message) from e
            raise ResourceError(message) from e

    return wrapper  # type: ignore[return-value]


@mcp.resource("spotify://user/current", mime_type="application/json")
@spotify_resource
def current_user() -> UserProfile:
    """Current user's profile."""
    return _profile()


@mcp.resource("spotify://playback/current", mime_type="application/json")
@spotify_resource
def current_playback_resource() -> PlaybackState:
    """Current playback state."""
    return _playback_state()


@mcp.resource("spotify://track/{track_id}", mime_type="application/json")
@spotify_resource
def track_resource(track_id: str) -> Track:
    """A single track by ID."""
    return parse_track(spotify_client.track(track_id))


@mcp.resource(
    "spotify://playlist/{playlist_id}",
    mime_type="application/json",
)
@spotify_resource
def playlist_resource(playlist_id: str) -> Playlist:
    """A playlist's metadata by ID (no tracks)."""
    return _playlist_metadata(playlist_id)


@mcp.resource("spotify://artist/{artist_id}", mime_type="application/json")
@spotify_resource
def artist_resource(artist_id: str) -> Artist:
    """An artist by ID."""
    return parse_artist(spotify_client.artist(artist_id))


@mcp.resource("spotify://album/{album_id}", mime_type="application/json")
@spotify_resource
def album_resource(album_id: str) -> Album:
    """An album's metadata by ID (no tracks)."""
    return parse_album(spotify_client.album(album_id))


# === PROMPTS ===


@mcp.prompt()
def discover_similar(artist: str) -> str:
    """Find artists similar to one you name, without a recommendations endpoint."""
    return f"""Find artists similar to {artist}.

This server does not expose related-artists or /recommendations; Spotify access
depends on the app. Subject to Spotify's AI-input policy, use the available tools:
1. get_artist for their genres
2. search_music with genre: and year: filters to find neighbours
3. get_top_items to bias toward what I already listen to — skip anything already
   in my top artists

Give me 8-10 artists with one line each on why, plus a representative track. Then
ask whether to save them or build a playlist."""


@mcp.prompt()
def taste_profile(time_range: str = "medium_term") -> str:
    """Summarize listening habits from top items and recent plays."""
    return f"""Profile my listening over {time_range}.

Use get_top_items for both 'artists' and 'tracks', plus get_recently_played. Tell me
the genres and moods that dominate, what has changed lately versus the longer ranges,
and two or three blind spots worth exploring. Be specific and skip the flattery."""


@mcp.prompt()
def create_mood_playlist(mood: str, genre: str = "", decade: str = "") -> str:
    """Create a playlist based on mood and preferences."""
    prompt = f"Create a Spotify playlist for a {mood} mood"

    if genre:
        prompt += f" with {genre} music"
    if decade:
        prompt += f" from the {decade}"

    return f"""{prompt}.

Workflow:
1. Use search_music with different queries to find diverse songs. Page with offset,
   advancing by the returned offset + limit (restricted apps serve at most 10 per page).
2. Create playlist with create_playlist
3. Add tracks with add_tracks_to_playlist (up to 100 tracks per call)

Consider:
1. Energy level for {mood} mood
2. {f"Focus on {genre}" if genre else "Genre variety"}
3. {f"Songs from {decade}" if decade else "Mix of eras"}
4. 15-20 songs with good flow"""


@mcp.prompt()
def analyze_large_playlist(playlist_id: str, analysis_type: str = "overview") -> str:
    """Analyze a large playlist efficiently using pagination."""
    return f"""Analyze playlist {playlist_id} with focus on {analysis_type}.

Step 1: Get overview
- Use get_playlist(playlist_id) for basic info including total_tracks

Step 2: Full analysis (if needed)
- For playlists >100 tracks, page through get_playlist_tracks:
  - get_playlist_tracks(playlist_id, limit=100, offset=0) for first 100
  - get_playlist_tracks(playlist_id, limit=100, offset=100) for next 100
  - Continue until you have all tracks or a sufficient sample

Step 3: Analysis
Based on analysis_type:
- "overview": Basic stats, genres, mood distribution
- "detailed": Track-by-track analysis, recommendations
- "duplicates": Find duplicate tracks across large playlist
- "mood": Analyze mood/energy progression through playlist"""


@mcp.prompt()
def discover_music_systematically(
    seed_query: str, exploration_depth: str = "medium"
) -> str:
    """Systematically discover music using search pagination."""
    return f"""Discover music related to "{seed_query}" with {exploration_depth} exploration.

Search Strategy with Pagination:
1. Initial search: search_music("{seed_query}", limit=20, offset=0)
2. Diverse results: Advance using each response's offset + limit:
   - Popular results: the first page
   - Hidden gems: the next few pages
   - Deep cuts: continue paging while more results are available

3. Related searches with pagination:
   - Artist names from initial results
   - Album names from initial results
   - Genre + decade combinations
   - Similar mood/energy descriptors

Exploration Depth:
- "light": 2-3 search queries, one page each
- "medium": 5-6 search queries, a few pages each
- "deep": 10+ search queries, explore more pages as needed

Advance by the returned offset + limit to avoid skips or duplicates. Prefer new query
variations over deep paging, and stop when you have enough good matches.

Output: Curated list of 15-25 discovered tracks with variety"""
