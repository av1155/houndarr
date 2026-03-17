"""Tests for the Readarr adapter functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from houndarr.clients.readarr import MissingBook, ReadarrClient
from houndarr.engine.adapters.readarr import (
    _author_context_label,
    _author_item_id,
    _book_label,
    adapt_cutoff,
    adapt_missing,
    dispatch_search,
    make_client,
)
from houndarr.engine.candidates import SearchCandidate
from houndarr.services.instances import Instance, InstanceType, ReadarrSearchMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_DATE = "2020-01-01T00:00:00Z"


def _make_instance(
    *,
    readarr_search_mode: ReadarrSearchMode = ReadarrSearchMode.book,
    unreleased_delay_hrs: int = 24,
) -> Instance:
    return Instance(
        id=4,
        name="Readarr Test",
        type=InstanceType.readarr,
        url="http://readarr:8787",
        api_key="test-key",
        enabled=True,
        batch_size=10,
        sleep_interval_mins=15,
        hourly_cap=20,
        cooldown_days=7,
        unreleased_delay_hrs=unreleased_delay_hrs,
        cutoff_enabled=False,
        cutoff_batch_size=5,
        cutoff_cooldown_days=21,
        cutoff_hourly_cap=1,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        readarr_search_mode=readarr_search_mode,
    )


def _make_book(
    *,
    book_id: int = 401,
    author_id: int = 60,
    author_name: str = "Test Author",
    title: str = "Test Book",
    release_date: str | None = _OLD_DATE,
) -> MissingBook:
    return MissingBook(
        book_id=book_id,
        author_id=author_id,
        author_name=author_name,
        title=title,
        release_date=release_date,
    )


# ---------------------------------------------------------------------------
# Label builders
# ---------------------------------------------------------------------------


class TestBookLabel:
    """Verify _book_label output."""

    def test_basic(self):
        item = _make_book(author_name="Tolkien", title="The Hobbit")
        assert _book_label(item) == "Tolkien - The Hobbit"

    def test_unknown_author(self):
        item = _make_book(author_name="", title="Some Book")
        assert _book_label(item) == "Unknown Author - Some Book"

    def test_unknown_book(self):
        item = _make_book(author_name="Author", title="")
        assert _book_label(item) == "Author - Unknown Book"

    def test_both_unknown(self):
        item = _make_book(author_name="", title="")
        assert _book_label(item) == "Unknown Author - Unknown Book"


class TestAuthorContextLabel:
    """Verify _author_context_label output."""

    def test_basic(self):
        item = _make_book(author_name="Asimov")
        assert _author_context_label(item) == "Asimov (author-context)"

    def test_unknown_author(self):
        item = _make_book(author_name="")
        assert _author_context_label(item) == "Unknown Author (author-context)"


# ---------------------------------------------------------------------------
# Author item ID
# ---------------------------------------------------------------------------


class TestAuthorItemId:
    """Verify _author_item_id formula."""

    def test_basic(self):
        assert _author_item_id(60) == -(60 * 1000)

    def test_negative_result(self):
        assert _author_item_id(60) == -60000

    def test_large_author(self):
        assert _author_item_id(999) == -999000

    def test_distinct_ids(self):
        assert _author_item_id(1) != _author_item_id(2)


# ---------------------------------------------------------------------------
# adapt_missing — book mode
# ---------------------------------------------------------------------------


class TestAdaptMissingBookMode:
    """Verify adapt_missing in book mode."""

    def test_basic_fields(self):
        instance = _make_instance()
        item = _make_book()
        candidate = adapt_missing(item, instance)

        assert isinstance(candidate, SearchCandidate)
        assert candidate.item_id == 401
        assert candidate.item_type == "book"
        assert candidate.label == "Test Author - Test Book"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "BookSearch", "book_id": 401}

    def test_released_no_unreleased_reason(self):
        instance = _make_instance()
        item = _make_book(release_date=_OLD_DATE)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_unreleased_within_delay(self):
        instance = _make_instance(unreleased_delay_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_book(release_date=recent)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason == "unreleased delay (24h)"

    def test_null_release_date_is_eligible(self):
        instance = _make_instance(unreleased_delay_hrs=24)
        item = _make_book(release_date=None)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_empty_release_date_is_eligible(self):
        instance = _make_instance(unreleased_delay_hrs=24)
        item = _make_book(release_date="")
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None

    def test_boundary_exact_delay(self):
        instance = _make_instance(unreleased_delay_hrs=24)
        exactly_past = (datetime.now(UTC) - timedelta(hours=24, seconds=1)).isoformat()
        item = _make_book(release_date=exactly_past)
        candidate = adapt_missing(item, instance)
        assert candidate.unreleased_reason is None


# ---------------------------------------------------------------------------
# adapt_missing — author-context mode
# ---------------------------------------------------------------------------


class TestAdaptMissingAuthorContext:
    """Verify adapt_missing in author-context mode."""

    def test_basic_fields(self):
        instance = _make_instance(readarr_search_mode=ReadarrSearchMode.author_context)
        item = _make_book(author_id=60)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _author_item_id(60)
        assert candidate.item_type == "book"
        assert candidate.label == "Test Author (author-context)"
        assert candidate.group_key == (60, 0)
        assert candidate.search_payload == {
            "command": "AuthorSearch",
            "author_id": 60,
        }

    def test_zero_author_id_falls_back(self):
        """When author_id is 0, falls back to book-mode behavior."""
        instance = _make_instance(readarr_search_mode=ReadarrSearchMode.author_context)
        item = _make_book(author_id=0)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == 401  # book_id
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "BookSearch"

    def test_large_author_id(self):
        """Large author IDs produce valid, distinct synthetic IDs."""
        instance = _make_instance(readarr_search_mode=ReadarrSearchMode.author_context)
        item = _make_book(author_id=999)
        candidate = adapt_missing(item, instance)

        assert candidate.item_id == _author_item_id(999)
        assert candidate.item_id < 0
        assert candidate.group_key == (999, 0)

    def test_distinct_authors_produce_distinct_ids(self):
        instance = _make_instance(readarr_search_mode=ReadarrSearchMode.author_context)
        item_a = _make_book(author_id=10)
        item_b = _make_book(author_id=20)
        assert adapt_missing(item_a, instance).item_id != adapt_missing(item_b, instance).item_id


# ---------------------------------------------------------------------------
# adapt_cutoff
# ---------------------------------------------------------------------------


class TestAdaptCutoff:
    """Verify adapt_cutoff always uses book mode."""

    def test_book_mode(self):
        instance = _make_instance()
        item = _make_book()
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 401
        assert candidate.item_type == "book"
        assert candidate.label == "Test Author - Test Book"
        assert candidate.group_key is None
        assert candidate.search_payload == {"command": "BookSearch", "book_id": 401}

    def test_ignores_author_context_mode(self):
        """Even with author_context mode, cutoff uses book-mode."""
        instance = _make_instance(readarr_search_mode=ReadarrSearchMode.author_context)
        item = _make_book(author_id=60)
        candidate = adapt_cutoff(item, instance)

        assert candidate.item_id == 401  # book_id, NOT synthetic author ID
        assert candidate.group_key is None
        assert candidate.search_payload["command"] == "BookSearch"

    def test_unreleased(self):
        instance = _make_instance(unreleased_delay_hrs=24)
        recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        item = _make_book(release_date=recent)
        candidate = adapt_cutoff(item, instance)
        assert candidate.unreleased_reason == "unreleased delay (24h)"


# ---------------------------------------------------------------------------
# dispatch_search
# ---------------------------------------------------------------------------


class TestDispatchSearch:
    """Verify dispatch_search calls the correct client method."""

    @pytest.mark.asyncio()
    async def test_book_search(self):
        client = AsyncMock(spec=ReadarrClient)
        candidate = SearchCandidate(
            item_id=401,
            item_type="book",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "BookSearch", "book_id": 401},
        )
        await dispatch_search(client, candidate)
        client.search.assert_awaited_once_with(401)

    @pytest.mark.asyncio()
    async def test_author_search(self):
        client = AsyncMock(spec=ReadarrClient)
        candidate = SearchCandidate(
            item_id=-60000,
            item_type="book",
            label="Test",
            unreleased_reason=None,
            group_key=(60, 0),
            search_payload={"command": "AuthorSearch", "author_id": 60},
        )
        await dispatch_search(client, candidate)
        client.search_author.assert_awaited_once_with(60)

    @pytest.mark.asyncio()
    async def test_unknown_command_raises(self):
        client = AsyncMock(spec=ReadarrClient)
        candidate = SearchCandidate(
            item_id=1,
            item_type="book",
            label="Test",
            unreleased_reason=None,
            group_key=None,
            search_payload={"command": "UnknownCommand"},
        )
        with pytest.raises(ValueError, match="Unknown Readarr search command"):
            await dispatch_search(client, candidate)


# ---------------------------------------------------------------------------
# make_client
# ---------------------------------------------------------------------------


class TestMakeClient:
    """Verify make_client returns a correctly configured ReadarrClient."""

    def test_returns_readarr_client(self):
        instance = _make_instance()
        client = make_client(instance)
        assert isinstance(client, ReadarrClient)
