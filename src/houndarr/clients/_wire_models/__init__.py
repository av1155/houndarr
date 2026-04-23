"""Pydantic wire models for *arr API responses.

Every response the clients parse goes through one of these models first.
Pydantic validates the JSON shape with readable errors when an *arr
drifts (field removed, type changed, null where a string was expected)
instead of the KeyError an ad hoc ``dict.get`` chain would produce
deep inside an adapter.

Field names are snake_case in Python; ``Field(alias="camelCase")`` maps
to the camelCase names the APIs serialise on the wire.  All models
share :class:`_ArrModel` which sets ``populate_by_name=True`` (so both
the alias and the Python name parse) and ``extra="ignore"`` (so the
many unused fields each *arr ships do not raise).

This ``__init__.py`` re-exports every public name so the package root
remains import-compatible with the pre-split flat module.  The per-app
submodules (``common``, ``sonarr``, ``radarr``, ``lidarr``, ``readarr``,
``whisparr_v2``, ``whisparr_v3``) own the per-endpoint shapes; callers
that want to narrow their import to one family can import from the
submodule directly.
"""

from __future__ import annotations

from houndarr.clients._wire_models.common import (
    ArrArtist,
    ArrAuthor,
    ArrSeries,
    PaginatedResponse,
    QueueStatus,
    SystemStatus,
)
from houndarr.clients._wire_models.lidarr import LidarrLibraryAlbum, LidarrWantedAlbum
from houndarr.clients._wire_models.radarr import RadarrLibraryMovie, RadarrWantedMovie
from houndarr.clients._wire_models.readarr import ReadarrLibraryBook, ReadarrWantedBook
from houndarr.clients._wire_models.sonarr import (
    SonarrLibraryEpisode,
    SonarrWantedEpisode,
)
from houndarr.clients._wire_models.whisparr_v2 import (
    WhisparrV2LibraryEpisode,
    WhisparrV2WantedEpisode,
)
from houndarr.clients._wire_models.whisparr_v3 import WhisparrV3LibraryMovie

__all__ = [
    "ArrArtist",
    "ArrAuthor",
    "ArrSeries",
    "LidarrLibraryAlbum",
    "LidarrWantedAlbum",
    "PaginatedResponse",
    "QueueStatus",
    "RadarrLibraryMovie",
    "RadarrWantedMovie",
    "ReadarrLibraryBook",
    "ReadarrWantedBook",
    "SonarrLibraryEpisode",
    "SonarrWantedEpisode",
    "SystemStatus",
    "WhisparrV2LibraryEpisode",
    "WhisparrV2WantedEpisode",
    "WhisparrV3LibraryMovie",
]
