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


class ArtistRef(TypedDict, total=False):
    """An artist as referenced inside a track/album (name always present)."""

    name: str
    id: str


class AlbumRef(TypedDict, total=False):
    """An album as referenced inside a track."""

    name: str
    id: str
    release_date: str


class _TrackRequired(TypedDict):
    name: str
    id: str


class TrackObject(_TrackRequired, total=False):
    artists: list[ArtistRef]
    album: AlbumRef
    release_date: str
    duration_ms: int
    popularity: int
    external_urls: ExternalUrls


class _ArtistRequired(TypedDict):
    name: str
    id: str


class ArtistObject(_ArtistRequired, total=False):
    genres: list[str]
    popularity: int
    followers: Followers


class Followers(TypedDict, total=False):
    total: int


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
