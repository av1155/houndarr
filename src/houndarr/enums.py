"""Consolidated enums for string literals used across the engine and routes.

Previously scattered as ``Literal[...]`` type aliases in ``engine/search_loop.py``
and ``engine/candidates.py``, and as bare string literals in route parsers and
the database CHECK constraints.  This module collects them into ``StrEnum``
subclasses so typos at call sites are caught by mypy, while values remain
backward-compatible with every existing ``CHECK`` constraint and log row
(``StrEnum`` instances compare equal to their string values).

Values are chosen to match the pre-existing strings exactly; no CHECK constraint
or migration is touched.  The enums here are byte-for-byte equivalent to the
``Literal`` aliases they replace.
"""

from __future__ import annotations

from enum import StrEnum


class SearchKind(StrEnum):
    """Pass kind for the search engine: missing / cutoff / upgrade."""

    missing = "missing"
    cutoff = "cutoff"
    upgrade = "upgrade"


class SearchAction(StrEnum):
    """Row action persisted to ``search_log.action``.

    Mirrors the ``CHECK(action IN ('searched','skipped','error','info'))``
    constraint on the ``search_log`` table.
    """

    searched = "searched"
    skipped = "skipped"
    error = "error"
    info = "info"


class CycleTrigger(StrEnum):
    """Why the current cycle started.

    Mirrors the ``CHECK(cycle_trigger IN ('scheduled','run_now','system'))``
    constraint on the ``search_log`` table.
    """

    scheduled = "scheduled"
    run_now = "run_now"
    system = "system"


class ItemType(StrEnum):
    """*arr item kind carried on a ``SearchCandidate`` and stored in DB rows.

    Mirrors the ``CHECK(item_type IN ('episode','movie','album','book',
    'whisparr_episode','whisparr_v3_movie'))`` constraint on the
    ``cooldowns`` and ``search_log`` tables.
    """

    episode = "episode"
    movie = "movie"
    album = "album"
    book = "book"
    whisparr_episode = "whisparr_episode"
    whisparr_v3_movie = "whisparr_v3_movie"
