"""TypedDicts for the Spotify Web API response shapes this server consumes.

spotipy returns untyped ``dict`` objects (typed as ``Any``), so these only
describe the *subset* of fields the tools actually read. Fields the code
treats as optional are marked ``total=False``; required keys live in a base
class that the optional class extends.
"""

from __future__ import annotations

from typing import TypedDict


class ExternalUrls(TypedDict, total=False):
    spotify: str


class _ArtistRefRequired(TypedDict):
    name: str


class ArtistRef(_ArtistRefRequired, total=False):
    """An artist as referenced inside a track/album (name always present)."""

    id: str


class _AlbumRefRequired(TypedDict):
    name: str


class AlbumRef(_AlbumRefRequired, total=False):
    """An album as referenced inside a track (name always present)."""

    id: str
    release_date: str


class Followers(TypedDict, total=False):
    total: int


class _TrackRequired(TypedDict):
    name: str
    # Local files and region-removed tracks carry a null id.
    id: str | None


class TrackObject(_TrackRequired, total=False):
    artists: list[ArtistRef]
    album: AlbumRef
    release_date: str
    duration_ms: int
    popularity: int
    external_urls: ExternalUrls
    is_local: bool


class _ArtistRequired(TypedDict):
    name: str
    id: str


class ArtistObject(_ArtistRequired, total=False):
    genres: list[str]
    popularity: int
    followers: Followers


class _AlbumRequired(TypedDict):
    name: str
    id: str


class AlbumObject(_AlbumRequired, total=False):
    artists: list[ArtistRef]
    release_date: str
    release_date_precision: str
    total_tracks: int
    album_type: str
    label: str
    genres: list[str]
    popularity: int
    external_urls: ExternalUrls
    tracks: TrackPage


class PlaylistOwner(TypedDict, total=False):
    display_name: str
    id: str


class TrackPage(TypedDict, total=False):
    """A paging object whose items are (or wrap) tracks."""

    total: int
    items: list[TrackObject]


class _PlaylistRequired(TypedDict):
    name: str
    id: str


class PlaylistObject(_PlaylistRequired, total=False):
    owner: PlaylistOwner
    description: str
    public: bool
    tracks: TrackPage


class _ShowRequired(TypedDict):
    name: str
    id: str


class ShowObject(_ShowRequired, total=False):
    # Restricted apps lose `publisher`.
    publisher: str
    description: str
    total_episodes: int
    media_type: str
    explicit: bool
    external_urls: ExternalUrls


class ResumePoint(TypedDict, total=False):
    fully_played: bool
    resume_position_ms: int


class _EpisodeRequired(TypedDict):
    name: str


class EpisodeObject(_EpisodeRequired, total=False):
    id: str
    description: str
    release_date: str
    duration_ms: int
    explicit: bool
    resume_point: ResumePoint
    external_urls: ExternalUrls
    # Only present on full episode objects (saved episodes), not on a show's list.
    show: ShowObject


class NamedRef(TypedDict, total=False):
    name: str


class _AudiobookRequired(TypedDict):
    name: str
    id: str


class AudiobookObject(_AudiobookRequired, total=False):
    authors: list[NamedRef]
    narrators: list[NamedRef]
    # Restricted apps lose `publisher`.
    publisher: str
    description: str
    edition: str
    total_chapters: int
    explicit: bool
    external_urls: ExternalUrls
