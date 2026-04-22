"""Value objects used across the engine + services + routes.

Currently the engine pipeline and the cooldown service pass the same
three-tuple ``(instance_id, item_id, item_type)`` around at ~30 call
sites.  ``ItemRef`` collapses that tuple into a single frozen value
object so future refactors cannot silently reorder the fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from houndarr.enums import ItemType


@dataclass(frozen=True, slots=True)
class ItemRef:
    """A reference to an *arr library item that the search engine tracks.

    Attributes:
        instance_id: Owning instance primary key (``instances.id``).
        item_id: App-specific item ID (episode/movie/album/book ID or
            the synthetic negative ID used for season/artist/author
            context groups).
        item_type: One of the values registered on the
            ``cooldowns.item_type`` and ``search_log.item_type`` CHECK
            constraints.
    """

    instance_id: int
    item_id: int
    item_type: ItemType

    def as_tuple(self) -> tuple[int, int, str]:
        """Return the legacy tuple shape used by SQL call sites.

        The string form of ``item_type`` is used because SQLite stores
        the CHECK-constrained column as TEXT; ``StrEnum`` comparison is
        value-identical to plain strings.
        """
        return (self.instance_id, self.item_id, self.item_type.value)
