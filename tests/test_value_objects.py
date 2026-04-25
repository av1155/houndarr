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

    def test_slots_limits_attribute_surface(self) -> None:
        """slots=True restricts which attributes the dataclass exposes."""
        ref = ItemRef(instance_id=1, item_id=1, item_type=ItemType.movie)
        assert set(ItemRef.__slots__) == {"instance_id", "item_id", "item_type"}
        # Frozen dataclass rejects even the declared attrs at runtime.
        with pytest.raises(AttributeError):
            ref.item_id = 2  # type: ignore[misc]

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
