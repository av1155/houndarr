"""Unit tests for the per-instance tag-based include / exclude filter.

Issues #637 and #833.  Covers the small helpers (``_tag_filter_skip_reason``,
``_resolve_tag_filter_ids`` and the per-pass ``TagFilterPolicy`` rules) in
isolation so the per-cycle filter behaviour is pinned independent of the
full ``_run_search_pass`` integration.

The end-to-end search-pass tests live in ``test_search_loop.py`` and
``test_tag_filter_per_pass.py`` and exercise the wired-up engine; this file
is the property-shaped pin for the filter logic alone.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from houndarr.engine.candidates import ItemType, SearchCandidate
from houndarr.engine.search_loop import (
    _NO_TAG_FILTER,
    _SKIP_TAG_FILTER,
    _PassTagFilter,
    _resolve_tag_filter_ids,
    _tag_filter_skip_reason,
)
from houndarr.enums import SearchKind
from houndarr.errors import ClientHTTPError, ClientTransportError, ClientValidationError
from houndarr.services.instances import Instance, TagFilterPolicy
from tests.test_engine.conftest import get_log_rows, make_instance


def _with_policy(instance: Instance, policy: TagFilterPolicy) -> Instance:
    return dataclasses.replace(instance, tag_filter=policy)


def _make_candidate(tags: tuple[int, ...] = ()) -> SearchCandidate:
    """Build a minimal :class:`SearchCandidate` for filter assertions."""
    return SearchCandidate(
        item_id=1,
        item_type=ItemType.movie,
        label="Fixture",
        unreleased_reason=None,
        group_key=None,
        search_payload={"command": "MoviesSearch", "movie_id": 1},
        tags=tags,
    )


class TestTagFilterSkipReason:
    """``_tag_filter_skip_reason`` decides per-candidate pass / fail."""

    def test_no_filter_passes_every_candidate(self) -> None:
        """Both directions ``None`` means the filter is a no-op."""
        assert _tag_filter_skip_reason(_make_candidate(), None, None) is None
        assert _tag_filter_skip_reason(_make_candidate(tags=(1, 2)), None, None) is None

    def test_include_match_passes(self) -> None:
        """A candidate with at least one included tag passes."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(1, 5)),
            frozenset({1, 9}),
            None,
        )
        assert result is None

    def test_include_no_match_fails(self) -> None:
        """A candidate with no matching include tag skips with the include reason."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(7, 8)),
            frozenset({1, 2}),
            None,
        )
        assert result == "tag filter (no included tag)"

    def test_zero_tags_fails_include_filter(self) -> None:
        """Items with no tags cannot match any include tag."""
        result = _tag_filter_skip_reason(_make_candidate(tags=()), frozenset({1}), None)
        assert result == "tag filter (no included tag)"

    def test_empty_include_set_fails(self) -> None:
        """An include set that resolved to zero IDs (all labels unknown) skips
        every candidate; otherwise an operator typo silently bypasses the
        filter, which is the wrong default for a safety control."""
        result = _tag_filter_skip_reason(_make_candidate(tags=(1, 2)), frozenset(), None)
        assert result == "tag filter (no included tag)"

    def test_exclude_match_fails(self) -> None:
        """A candidate with any excluded tag skips with the exclude reason."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(1, 2)),
            None,
            frozenset({2}),
        )
        assert result == "tag filter (excluded tag)"

    def test_exclude_no_match_passes(self) -> None:
        """Candidates with no excluded tag pass even when the exclude set is set."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(7, 8)),
            None,
            frozenset({1, 2}),
        )
        assert result is None

    def test_zero_tags_passes_exclude_filter(self) -> None:
        """Untagged items have nothing to exclude on."""
        result = _tag_filter_skip_reason(_make_candidate(tags=()), None, frozenset({1, 2}))
        assert result is None

    def test_include_evaluated_before_exclude(self) -> None:
        """When both filters apply, include is checked first.

        A candidate that fails include should report the include reason
        rather than the exclude reason, even if the exclude filter would
        also block it.  This is the contract the engine relies on for
        consistent skip-log breakdowns.
        """
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(9,)),
            frozenset({1, 2}),
            frozenset({9}),
        )
        assert result == "tag filter (no included tag)"

    def test_both_filters_pass(self) -> None:
        """Tag in include set, no tag in exclude set: candidate passes."""
        result = _tag_filter_skip_reason(
            _make_candidate(tags=(1,)),
            frozenset({1}),
            frozenset({99}),
        )
        assert result is None


class TestTagFilterPolicyPerPass:
    """``exclude_for`` adds a pass's own labels to the instance-wide exclude (#833)."""

    @pytest.mark.parametrize("kind", list(SearchKind))
    def test_defaults_are_empty_on_every_pass(self, kind: SearchKind) -> None:
        policy = TagFilterPolicy()
        assert policy.pass_exclude(kind) == ()
        assert policy.exclude_for(kind) == ()

    @pytest.mark.parametrize("kind", list(SearchKind))
    def test_without_own_exclude_returns_instance_wide_list(self, kind: SearchKind) -> None:
        policy = TagFilterPolicy(include=("1080p",), exclude=("uncut",))
        assert policy.exclude_for(kind) == ("uncut",)

    def test_own_exclude_is_appended_after_instance_wide(self) -> None:
        policy = TagFilterPolicy(exclude=("skip", "private"), cutoff_exclude=("no-upgrade",))
        assert policy.exclude_for(SearchKind.cutoff) == ("skip", "private", "no-upgrade")

    def test_union_dedupes_keeping_first_occurrence(self) -> None:
        policy = TagFilterPolicy(exclude=("a", "b"), upgrade_exclude=("b", "c", "a"))
        assert policy.exclude_for(SearchKind.upgrade) == ("a", "b", "c")

    def test_own_exclude_stays_on_its_pass(self) -> None:
        policy = TagFilterPolicy(cutoff_exclude=("no-upgrade",))
        assert policy.exclude_for(SearchKind.missing) == ()
        assert policy.exclude_for(SearchKind.cutoff) == ("no-upgrade",)
        assert policy.exclude_for(SearchKind.upgrade) == ()

    def test_each_pass_reads_its_own_field(self) -> None:
        policy = TagFilterPolicy(
            exclude=("all",),
            missing_exclude=("m",),
            cutoff_exclude=("c",),
            upgrade_exclude=("u",),
        )
        assert policy.pass_exclude(SearchKind.missing) == ("m",)
        assert policy.pass_exclude(SearchKind.cutoff) == ("c",)
        assert policy.pass_exclude(SearchKind.upgrade) == ("u",)
        assert policy.exclude_for(SearchKind.missing) == ("all", "m")
        assert policy.exclude_for(SearchKind.cutoff) == ("all", "c")
        assert policy.exclude_for(SearchKind.upgrade) == ("all", "u")


class TestResolveTagFilterIds:
    """``_resolve_tag_filter_ids`` runs once per cycle.

    The function takes the instance's labels, calls the adapter's client
    ``get_tags()`` once, and returns the resolved sets.  These tests use
    a minimal stub adapter plus a real :class:`TagFilterPolicy` so the
    label normalisation + degradation behaviour is locked.
    """

    @pytest.fixture()
    def instance(self) -> Instance:
        return make_instance()

    def _stub_adapter(self, *, get_tags_result: Any = None, raises: Any = None) -> Any:
        """Build a minimal adapter whose ``make_client`` yields a stub client.

        ``get_tags_result`` is the dict the client returns; ``raises`` is
        an exception to throw instead (for the failure-mode test).
        ``make_client`` is wired as a ``MagicMock`` (not a lambda) so the
        short-circuit assertions in the no-filter test can inspect call
        history.
        """
        from unittest.mock import MagicMock  # noqa: PLC0415

        client = AsyncMock()
        if raises is not None:
            client.get_tags = AsyncMock(side_effect=raises)
        else:
            client.get_tags = AsyncMock(return_value=get_tags_result or {})

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=None)

        adapter = AsyncMock()
        adapter.make_client = MagicMock(return_value=ctx)
        return adapter

    @pytest.mark.asyncio()
    async def test_empty_filter_short_circuits(self, instance: Any, seeded_instances: None) -> None:
        """With no labels anywhere every pass is unfiltered and no client
        is opened (and therefore no /tag GET fires)."""
        adapter = self._stub_adapter()
        resolved = await _resolve_tag_filter_ids(
            instance, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert resolved == dict.fromkeys(SearchKind, _NO_TAG_FILTER)
        adapter.make_client.assert_not_called()

    @pytest.mark.asyncio()
    async def test_resolves_labels_to_ids(self, seeded_instances: None) -> None:
        """Known labels are mapped to their IDs on every enabled pass.

        Both sides of the lookup are normalised to lowercase before the
        dict.get fires: the *arr's labels are lowercased in
        :func:`ArrClient.get_tags`, and the operator's labels are
        lowercased in :func:`validate_tag_filter`.  The match itself
        is a plain case-sensitive ``dict.get`` against pre-lowered keys.
        """
        scoped = _with_policy(
            make_instance(cutoff_enabled=True),
            TagFilterPolicy(include=("1080p",), exclude=("uncut",)),
        )
        adapter = self._stub_adapter(
            get_tags_result={"1080p": 1, "4k": 2, "uncut": 7},
        )
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        both = _PassTagFilter(frozenset({1}), frozenset({7}))
        assert resolved == {
            SearchKind.missing: both,
            SearchKind.cutoff: both,
            SearchKind.upgrade: _NO_TAG_FILTER,
        }

    @pytest.mark.asyncio()
    async def test_unknown_labels_become_empty_set(
        self, instance: Any, seeded_instances: None
    ) -> None:
        """An include list of only-unknown labels resolves to an empty set.

        The downstream filter then skips every candidate (no item can
        match a label that does not exist on this *arr), which is the
        right safety default for an operator typo.
        """
        scoped = _with_policy(instance, TagFilterPolicy(include=("nope",), exclude=()))
        adapter = self._stub_adapter(get_tags_result={"1080p": 1})
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert resolved[SearchKind.missing] == _PassTagFilter(frozenset(), None)

    @pytest.mark.asyncio()
    async def test_get_tags_transport_error_propagates(
        self, instance: Any, seeded_instances: None
    ) -> None:
        """An unreachable *arr surfaces to the supervisor like any other request.

        Swallowing it would let a cycle whose every pass is labelled
        finish "successfully", hiding the outage and skipping the quick
        reconnect retry.
        """
        scoped = _with_policy(instance, TagFilterPolicy(include=("1080p",)))
        adapter = self._stub_adapter(raises=ClientTransportError("network down"))
        with pytest.raises(ClientTransportError):
            await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")
        assert await get_log_rows() == []

    @pytest.mark.parametrize(
        "exc",
        [
            ClientHTTPError("get_tags: HTTP 500 from /api/v3/tag"),
            ClientValidationError("get_tags: non-JSON body from /api/v3/tag"),
            httpx.InvalidURL("bad url"),
        ],
        ids=["http", "validation", "invalid-url"],
    )
    @pytest.mark.asyncio()
    async def test_fetch_failure_skips_only_labelled_enabled_passes(
        self, exc: Exception, seeded_instances: None
    ) -> None:
        """Passes with labels sit the cycle out; the unlabelled pass runs unfiltered."""
        scoped = _with_policy(
            make_instance(cutoff_enabled=True, upgrade_enabled=True),
            TagFilterPolicy(cutoff_exclude=("no-upgrade",), upgrade_exclude=("keep-sd",)),
        )
        adapter = self._stub_adapter(raises=exc)
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="run_now"
        )

        assert resolved == {
            SearchKind.missing: _NO_TAG_FILTER,
            SearchKind.cutoff: _SKIP_TAG_FILTER,
            SearchKind.upgrade: _SKIP_TAG_FILTER,
        }
        rows = await get_log_rows()
        assert len(rows) == 1
        assert rows[0]["action"] == "info"
        assert rows[0]["reason"] == "tag filter (fetch failed)"
        assert rows[0]["message"] == (
            f"tag filter unavailable, skipping the cutoff, upgrade passes this cycle: {exc}"
        )
        assert rows[0]["cycle_id"] == "c1"
        assert rows[0]["cycle_trigger"] == "run_now"

    @pytest.mark.asyncio()
    async def test_fetch_failure_with_instance_wide_label_skips_every_enabled_pass(
        self, seeded_instances: None
    ) -> None:
        scoped = _with_policy(
            make_instance(cutoff_enabled=True), TagFilterPolicy(include=("1080p",))
        )
        adapter = self._stub_adapter(raises=ClientHTTPError("get_tags: HTTP 503 from /api/v3/tag"))
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )

        assert resolved[SearchKind.missing] == _SKIP_TAG_FILTER
        assert resolved[SearchKind.cutoff] == _SKIP_TAG_FILTER
        assert resolved[SearchKind.upgrade] == _NO_TAG_FILTER
        rows = await get_log_rows()
        assert [r["message"] for r in rows] == [
            "tag filter unavailable, skipping the missing, cutoff passes this cycle: "
            "get_tags: HTTP 503 from /api/v3/tag"
        ]

    @pytest.mark.asyncio()
    async def test_fetch_failure_single_pass_message(
        self, instance: Any, seeded_instances: None
    ) -> None:
        scoped = _with_policy(instance, TagFilterPolicy(exclude=("uncut",)))
        adapter = self._stub_adapter(raises=ClientHTTPError("get_tags: HTTP 500 from /api/v3/tag"))
        await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")

        rows = await get_log_rows()
        assert [r["message"] for r in rows] == [
            "tag filter unavailable, skipping the missing pass this cycle: "
            "get_tags: HTTP 500 from /api/v3/tag"
        ]

    @pytest.mark.asyncio()
    async def test_fetch_failure_message_redacts_url_credentials(
        self, instance: Any, seeded_instances: None
    ) -> None:
        scoped = _with_policy(instance, TagFilterPolicy(exclude=("uncut",)))
        adapter = self._stub_adapter(raises=httpx.InvalidURL("bad url http://user:secret@arr/x"))
        await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")

        message = (await get_log_rows())[0]["message"]
        assert "secret" not in message
        assert message.startswith("tag filter unavailable, skipping the missing pass this cycle: ")

    @pytest.mark.asyncio()
    async def test_no_get_when_only_a_disabled_pass_has_own_labels(
        self, instance: Any, seeded_instances: None
    ) -> None:
        scoped = _with_policy(
            instance,
            TagFilterPolicy(cutoff_exclude=("no-upgrade",), upgrade_exclude=("no-upgrade",)),
        )
        adapter = self._stub_adapter()
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )

        assert resolved == dict.fromkeys(SearchKind, _NO_TAG_FILTER)
        adapter.make_client.assert_not_called()
        assert await get_log_rows() == []

    @pytest.mark.asyncio()
    async def test_own_label_on_enabled_pass_triggers_one_get(self, seeded_instances: None) -> None:
        scoped = _with_policy(
            make_instance(cutoff_enabled=True), TagFilterPolicy(cutoff_exclude=("no-upgrade",))
        )
        adapter = self._stub_adapter(get_tags_result={"no-upgrade": 5})
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )

        assert adapter.make_client.call_count == 1
        assert resolved[SearchKind.missing] == _NO_TAG_FILTER
        assert resolved[SearchKind.cutoff] == _PassTagFilter(None, frozenset({5}))

    @pytest.mark.asyncio()
    async def test_one_get_resolves_every_pass(self, seeded_instances: None) -> None:
        scoped = _with_policy(
            make_instance(cutoff_enabled=True, upgrade_enabled=True),
            TagFilterPolicy(
                exclude=("all-out",),
                missing_exclude=("no-backfill",),
                cutoff_exclude=("no-upgrade",),
                upgrade_exclude=("no-upgrade", "keep-sd"),
            ),
        )
        adapter = self._stub_adapter(
            get_tags_result={"all-out": 3, "no-backfill": 4, "no-upgrade": 5, "keep-sd": 6}
        )
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )

        assert adapter.make_client.call_count == 1
        assert resolved == {
            SearchKind.missing: _PassTagFilter(None, frozenset({3, 4})),
            SearchKind.cutoff: _PassTagFilter(None, frozenset({3, 5})),
            SearchKind.upgrade: _PassTagFilter(None, frozenset({3, 5, 6})),
        }
        assert await get_log_rows() == []

    @pytest.mark.asyncio()
    async def test_instance_wide_exclude_survives_unknown_own_exclude(
        self, seeded_instances: None
    ) -> None:
        """A typo in a pass's own list can never drop an instance-wide exclusion."""
        scoped = _with_policy(
            make_instance(cutoff_enabled=True),
            TagFilterPolicy(exclude=("skip",), cutoff_exclude=("no-upgarde",)),
        )
        adapter = self._stub_adapter(get_tags_result={"skip": 3})
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )
        assert resolved[SearchKind.cutoff].exclude_ids == frozenset({3})

    @pytest.mark.asyncio()
    async def test_unknown_own_label_carries_pass_suffix(self, seeded_instances: None) -> None:
        scoped = _with_policy(
            make_instance(cutoff_enabled=True), TagFilterPolicy(cutoff_exclude=("no-upgarde",))
        )
        adapter = self._stub_adapter(get_tags_result={"no-upgrade": 5})
        await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")

        rows = await get_log_rows()
        assert [(r["reason"], r["message"]) for r in rows] == [
            (
                "tag filter (unknown label)",
                "tag filter: unknown label(s) on Test Instance: no-upgarde (cutoff exclude)",
            )
        ]

    @pytest.mark.asyncio()
    async def test_label_unknown_in_both_lists_is_listed_bare_once(
        self, seeded_instances: None
    ) -> None:
        scoped = _with_policy(
            make_instance(cutoff_enabled=True),
            TagFilterPolicy(exclude=("nope",), cutoff_exclude=("nope",)),
        )
        adapter = self._stub_adapter(get_tags_result={"other": 1})
        await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")

        rows = await get_log_rows()
        assert [r["message"] for r in rows] == [
            "tag filter: unknown label(s) on Test Instance: nope"
        ]

    @pytest.mark.asyncio()
    async def test_unknown_labels_from_several_sources_share_one_row(
        self, seeded_instances: None
    ) -> None:
        """Instance-wide typos come first, bare; then each pass's own, in cycle order."""
        scoped = _with_policy(
            make_instance(cutoff_enabled=True, upgrade_enabled=True),
            TagFilterPolicy(
                include=("zz-typo",),
                cutoff_exclude=("aa-typo",),
                upgrade_exclude=("mm-typo", "aa-typo"),
            ),
        )
        adapter = self._stub_adapter(get_tags_result={"other": 1})
        await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")

        rows = await get_log_rows()
        assert [r["message"] for r in rows] == [
            "tag filter: unknown label(s) on Test Instance: zz-typo, "
            "aa-typo (cutoff exclude), aa-typo (upgrade exclude), mm-typo (upgrade exclude)"
        ]

    @pytest.mark.asyncio()
    async def test_disabled_pass_labels_are_not_reported(
        self, instance: Any, seeded_instances: None
    ) -> None:
        scoped = _with_policy(
            instance, TagFilterPolicy(exclude=("uncut",), upgrade_exclude=("typo",))
        )
        adapter = self._stub_adapter(get_tags_result={"uncut": 7})
        resolved = await _resolve_tag_filter_ids(
            scoped, adapter, cycle_id="c1", cycle_trigger="scheduled"
        )

        assert adapter.make_client.call_count == 1
        assert resolved[SearchKind.missing] == _PassTagFilter(None, frozenset({7}))
        assert await get_log_rows() == []

    @pytest.mark.pinning()
    @pytest.mark.asyncio()
    async def test_unknown_label_message_without_own_labels_is_unchanged(
        self, instance: Any, seeded_instances: None
    ) -> None:
        """Unknown instance-wide labels share one sorted, deduped info row."""
        scoped = _with_policy(
            instance, TagFilterPolicy(include=("1080p", "b-typo"), exclude=("a-typo", "b-typo"))
        )
        adapter = self._stub_adapter(get_tags_result={"1080p": 1})
        await _resolve_tag_filter_ids(scoped, adapter, cycle_id="c1", cycle_trigger="scheduled")

        rows = await get_log_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row["action"] == "info"
        assert row["item_id"] is None
        assert row["reason"] == "tag filter (unknown label)"
        assert row["message"] == "tag filter: unknown label(s) on Test Instance: a-typo, b-typo"
        assert row["cycle_id"] == "c1"
        assert row["cycle_trigger"] == "scheduled"
