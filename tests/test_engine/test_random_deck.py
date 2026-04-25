"""Pin the stratified-shuffle invariants for ``SearchOrder.random``.

The engine draws each cycle's start page (and any subsequent walk pages)
from a per-(instance, kind) deck of all pages ``[1, max_page]``.  These
tests pin the four properties the design buys:

1. Within a round the deck contains every page exactly once: no page is
   skipped and no page is visited twice before the round ends.
2. When the deck is exhausted the engine reshuffles automatically so
   long-run behaviour stays uniformly random.
3. When ``max_page`` changes (the wanted-list grew or shrank) the deck
   rebuilds against the new size on the very next draw.
4. The per-(instance, kind) keying isolates Sonarr-missing from
   Sonarr-cutoff and from a second Sonarr instance: one's progress
   does not leak into another's.
"""

from __future__ import annotations

import pytest

from houndarr.engine.search_loop import (
    _draw_next_random_page,
    _random_decks,
    _reset_random_deck,
)


@pytest.fixture(autouse=True)
def _clean_decks() -> None:
    """Wipe the module-level deck cache before every test in this file."""
    _random_decks.clear()


def test_round_permutes_every_page_exactly_once() -> None:
    """One round of draws produces a permutation of [1..max_page]."""
    max_page = 10
    drawn = [_draw_next_random_page(1, "missing", max_page) for _ in range(max_page)]
    assert sorted(drawn) == list(range(1, max_page + 1))
    assert len(set(drawn)) == max_page


def test_consecutive_draws_do_not_repeat_within_a_round() -> None:
    """No page appears twice in the same round, even at small max_page."""
    max_page = 3
    seen: set[int] = set()
    for _ in range(max_page):
        page = _draw_next_random_page(1, "missing", max_page)
        assert page not in seen, f"page {page} repeated within one round"
        seen.add(page)


def test_deck_reshuffles_when_exhausted() -> None:
    """After ``max_page`` draws the next call kicks off a fresh round."""
    max_page = 4
    first_round = [_draw_next_random_page(1, "missing", max_page) for _ in range(max_page)]
    second_round = [_draw_next_random_page(1, "missing", max_page) for _ in range(max_page)]
    assert sorted(first_round) == list(range(1, max_page + 1))
    assert sorted(second_round) == list(range(1, max_page + 1))


def test_max_page_change_rebuilds_deck() -> None:
    """Growing or shrinking the wanted-list invalidates any partial round."""
    # Draw three pages out of a five-page round.
    for _ in range(3):
        _draw_next_random_page(1, "missing", 5)
    assert len(_random_decks[(1, "missing")].remaining) == 2
    # Now the wanted-list grew to 8 pages.  The next draw must come from
    # a fresh shuffle of [1..8], not from the leftover [1..5] tail.
    page = _draw_next_random_page(1, "missing", 8)
    assert 1 <= page <= 8
    assert _random_decks[(1, "missing")].max_page == 8
    assert len(_random_decks[(1, "missing")].remaining) == 7


def test_decks_are_isolated_per_instance_and_kind() -> None:
    """Three deck keys carry independent state and never share draws."""
    # Drain instance 1's missing deck completely.
    drained = sorted(_draw_next_random_page(1, "missing", 4) for _ in range(4))
    assert drained == [1, 2, 3, 4]
    assert len(_random_decks[(1, "missing")].remaining) == 0
    # Instance 1's cutoff deck is untouched: starts a fresh round on first draw.
    cutoff_first = _draw_next_random_page(1, "cutoff", 4)
    assert 1 <= cutoff_first <= 4
    assert len(_random_decks[(1, "cutoff")].remaining) == 3
    # Instance 2's missing deck is also independent.
    other_first = _draw_next_random_page(2, "missing", 4)
    assert 1 <= other_first <= 4
    assert len(_random_decks[(2, "missing")].remaining) == 3
    # Instance 1's exhausted missing deck reshuffles on the next draw.
    next_round_first = _draw_next_random_page(1, "missing", 4)
    assert 1 <= next_round_first <= 4
    assert len(_random_decks[(1, "missing")].remaining) == 3


def test_reset_clears_one_key_only() -> None:
    """``_reset_random_deck`` drops one (instance, kind) pair, no others."""
    _draw_next_random_page(1, "missing", 5)
    _draw_next_random_page(1, "cutoff", 5)
    _reset_random_deck(1, "missing")
    assert (1, "missing") not in _random_decks
    assert (1, "cutoff") in _random_decks


def test_max_page_one_repeats_page_one_every_draw() -> None:
    """Single-page wanted lists draw page 1 forever; no degenerate crashes."""
    for _ in range(10):
        assert _draw_next_random_page(1, "missing", 1) == 1
