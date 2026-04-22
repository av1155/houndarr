"""Tests for the consolidated houndarr.enums module.

Verifies that each StrEnum value compares equal to its legacy string
literal, keeping every CHECK constraint and log-row value untouched.
"""

from __future__ import annotations

import pytest

from houndarr.enums import CycleTrigger, ItemType, SearchAction, SearchKind


class TestSearchKind:
    @pytest.mark.parametrize("value", ["missing", "cutoff", "upgrade"])
    def test_values_match_literal(self, value: str) -> None:
        assert SearchKind(value).value == value

    def test_str_equality_with_literal(self) -> None:
        """StrEnum members are equal to their string values for legacy call sites."""
        assert SearchKind.missing == "missing"
        assert SearchKind.cutoff == "cutoff"
        assert SearchKind.upgrade == "upgrade"

    def test_unknown_value_raises(self) -> None:
        with pytest.raises(ValueError):
            SearchKind("bogus")


class TestSearchAction:
    @pytest.mark.parametrize("value", ["searched", "skipped", "error", "info"])
    def test_values_match_literal(self, value: str) -> None:
        assert SearchAction(value).value == value

    def test_str_equality_with_literal(self) -> None:
        assert SearchAction.searched == "searched"
        assert SearchAction.skipped == "skipped"
        assert SearchAction.error == "error"
        assert SearchAction.info == "info"


class TestCycleTrigger:
    @pytest.mark.parametrize("value", ["scheduled", "run_now", "system"])
    def test_values_match_literal(self, value: str) -> None:
        assert CycleTrigger(value).value == value

    def test_str_equality_with_literal(self) -> None:
        assert CycleTrigger.scheduled == "scheduled"
        assert CycleTrigger.run_now == "run_now"
        assert CycleTrigger.system == "system"


class TestItemType:
    @pytest.mark.parametrize(
        "value",
        [
            "episode",
            "movie",
            "album",
            "book",
            "whisparr_episode",
            "whisparr_v3_movie",
        ],
    )
    def test_values_match_literal(self, value: str) -> None:
        assert ItemType(value).value == value

    def test_str_equality_with_literal(self) -> None:
        assert ItemType.episode == "episode"
        assert ItemType.movie == "movie"
        assert ItemType.whisparr_v3_movie == "whisparr_v3_movie"
