"""Readarr wire models: /wanted book records + library book shape."""

from __future__ import annotations

from pydantic import Field

from houndarr.clients._wire_models.common import (
    ArrAuthor,
    _ArrModel,
    _WireBookStatistics,
)


class ReadarrWantedBook(_ArrModel):
    id: int
    author_id: int | None = Field(default=None, alias="authorId")
    author: ArrAuthor | None = None
    title: str | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")


class ReadarrLibraryBook(_ArrModel):
    id: int
    author_id: int | None = Field(default=None, alias="authorId")
    author: ArrAuthor | None = None
    title: str | None = None
    monitored: bool | None = None
    release_date: str | None = Field(default=None, alias="releaseDate")
    statistics: _WireBookStatistics | None = None
