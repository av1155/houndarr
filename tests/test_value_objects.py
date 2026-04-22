"""Tests for houndarr.value_objects."""

from __future__ import annotations

import pytest

from houndarr.enums import ItemType
from houndarr.value_objects import ItemRef


class TestItemRef:
    def test_frozen(self) -> None:
        ref = ItemRef(instance_id=1, item_id=100, item_type=ItemType.movie)
        with pytest.raises(AttributeError):
            ref.instance_id = 2  # type: ignore[misc]

    def test_fields(self) -> None:
        ref = ItemRef(instance_id=7, item_id=42, item_type=ItemType.episode)
        assert ref.instance_id == 7
        assert ref.item_id == 42
        assert ref.item_type == ItemType.episode

    def test_slots(self) -> None:
        """Slots dataclass rejects arbitrary attribute assignment."""
        ref = ItemRef(instance_id=1, item_id=1, item_type=ItemType.movie)
        with pytest.raises(AttributeError):
            ref.extra = "no"  # type: ignore[attr-defined]

    def test_as_tuple_returns_str_valued_item_type(self) -> None:
        ref = ItemRef(instance_id=1, item_id=5, item_type=ItemType.album)
        assert ref.as_tuple() == (1, 5, "album")

    def test_equal_when_fields_match(self) -> None:
        a = ItemRef(instance_id=1, item_id=100, item_type=ItemType.movie)
        b = ItemRef(instance_id=1, item_id=100, item_type=ItemType.movie)
        assert a == b

    def test_hashable(self) -> None:
        """Frozen dataclasses with hashable fields are usable as set/dict keys."""
        ref = ItemRef(instance_id=1, item_id=1, item_type=ItemType.movie)
        assert {ref: 1}[ref] == 1
