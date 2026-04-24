"""Pin the pure helpers in routes/api/logs.py.

Locks the parser helpers (``parse_instance_ids`` /
``parse_search_kind`` / ``parse_cycle_trigger`` /
``parse_hide_system``), the summary builder (``summarize_rows``),
the limit clamp (``compute_load_more_limit``), and the HTMX 422
partial shape (``_partial_validation_error``) so the dynamic SQL
builder in :mod:`houndarr.services.log_query` and the helpers in
the route cannot drift apart.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from houndarr.routes.api.logs import (
    _partial_validation_error,
    parse_cycle_trigger,
    parse_hide_skipped,
    parse_hide_system,
    parse_instance_ids,
    parse_search_kind,
)
from houndarr.services.log_query import compute_load_more_limit, summarize_rows

pytestmark = pytest.mark.pinning


# Parsers


class TestParseInstanceIds:
    def test_none_returns_empty_tuple(self) -> None:
        assert parse_instance_ids(None) == ()

    def test_empty_list_returns_empty_tuple(self) -> None:
        assert parse_instance_ids([]) == ()

    def test_empty_string_filtered_out(self) -> None:
        # The native "All instances" <option value=""> still submits an
        # empty string; it must not introduce a phantom zero into the
        # WHERE clause.
        assert parse_instance_ids([""]) == ()

    def test_single_integer_string_parses(self) -> None:
        assert parse_instance_ids(["42"]) == (42,)

    def test_multiple_values_preserve_order(self) -> None:
        assert parse_instance_ids(["3", "1", "2"]) == (3, 1, 2)

    def test_duplicates_deduped(self) -> None:
        # Order-preserving dedupe keeps the first occurrence so the SQL
        # placeholder sequence is deterministic across retries.
        assert parse_instance_ids(["1", "2", "1", "2"]) == (1, 2)

    def test_mixed_empties_skipped(self) -> None:
        assert parse_instance_ids(["", "42", ""]) == (42,)

    def test_negative_accepted(self) -> None:
        """Pinning quirk: negative ints pass the int() cast; upstream should gate."""
        assert parse_instance_ids(["-1"]) == (-1,)

    def test_non_integer_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            parse_instance_ids(["abc"])
        assert exc.value.status_code == 422


class TestParseSearchKind:
    @pytest.mark.parametrize("kind", ["missing", "cutoff", "upgrade"])
    def test_known_kinds_accepted(self, kind: str) -> None:
        assert parse_search_kind(kind) == kind

    def test_none_returns_none(self) -> None:
        assert parse_search_kind(None) is None

    def test_empty_returns_none(self) -> None:
        assert parse_search_kind("") is None

    def test_unknown_kind_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            parse_search_kind("bogus")
        assert exc.value.status_code == 422


class TestParseCycleTrigger:
    @pytest.mark.parametrize("trigger", ["scheduled", "run_now", "system"])
    def test_known_triggers_accepted(self, trigger: str) -> None:
        assert parse_cycle_trigger(trigger) == trigger

    def test_none_returns_none(self) -> None:
        assert parse_cycle_trigger(None) is None

    def test_empty_returns_none(self) -> None:
        assert parse_cycle_trigger("") is None

    def test_unknown_trigger_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            parse_cycle_trigger("manual")
        assert exc.value.status_code == 422


class TestParseHideSystem:
    @pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE", "yes", "on", " On "])
    def test_truthy_values(self, raw: str) -> None:
        assert parse_hide_system(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "off"])
    def test_falsy_values(self, raw: str) -> None:
        assert parse_hide_system(raw) is False

    def test_none_returns_false(self) -> None:
        assert parse_hide_system(None) is False

    def test_empty_returns_false(self) -> None:
        assert parse_hide_system("") is False

    def test_garbage_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            parse_hide_system("maybe")
        assert exc.value.status_code == 422


class TestParseHideSkipped:
    @pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE", "yes", "on", " On "])
    def test_truthy_values(self, raw: str) -> None:
        assert parse_hide_skipped(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "off"])
    def test_falsy_values(self, raw: str) -> None:
        assert parse_hide_skipped(raw) is False

    def test_none_returns_false(self) -> None:
        assert parse_hide_skipped(None) is False

    def test_empty_returns_false(self) -> None:
        assert parse_hide_skipped("") is False

    def test_garbage_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            parse_hide_skipped("maybe")
        assert exc.value.status_code == 422


# compute_load_more_limit


class TestComputeLoadMoreLimit:
    def test_small_limit_capped_at_100_upper(self) -> None:
        assert compute_load_more_limit(50) == 50

    def test_at_100_returns_100(self) -> None:
        assert compute_load_more_limit(100) == 100

    def test_over_100_capped_to_100(self) -> None:
        assert compute_load_more_limit(500) == 100

    def test_zero_clamped_to_one(self) -> None:
        """Minimum is 1 even if caller passes 0 or negative."""
        assert compute_load_more_limit(0) == 1
        assert compute_load_more_limit(-50) == 1


# summarize_rows


class TestSummarizeRows:
    def test_empty_rows_yields_zero_everything(self) -> None:
        summary = summarize_rows([])
        assert summary == {
            "total_rows": 0,
            "searched_rows": 0,
            "skipped_rows": 0,
            "error_rows": 0,
            "info_rows": 0,
            "total_cycles": 0,
            "searched_cycles": 0,
            "skip_only_cycles": 0,
        }

    def test_counts_each_action(self) -> None:
        rows: list[dict[str, Any]] = [
            {"action": "searched", "cycle_id": "c1", "cycle_progress": "progress"},
            {"action": "skipped", "cycle_id": "c1", "cycle_progress": ""},
            {"action": "error", "cycle_id": "c2", "cycle_progress": ""},
            {"action": "info", "cycle_id": None, "cycle_progress": ""},
        ]
        summary = summarize_rows(rows)
        assert summary["total_rows"] == 4
        assert summary["searched_rows"] == 1
        assert summary["skipped_rows"] == 1
        assert summary["error_rows"] == 1
        assert summary["info_rows"] == 1

    def test_cycle_with_any_progress_is_searched(self) -> None:
        """If any row in a cycle has cycle_progress='progress', the cycle counts as searched."""
        rows: list[dict[str, Any]] = [
            {"action": "skipped", "cycle_id": "c1", "cycle_progress": ""},
            {"action": "searched", "cycle_id": "c1", "cycle_progress": "progress"},
        ]
        summary = summarize_rows(rows)
        assert summary["total_cycles"] == 1
        assert summary["searched_cycles"] == 1
        assert summary["skip_only_cycles"] == 0

    def test_cycle_with_only_skips_is_skip_only(self) -> None:
        rows: list[dict[str, Any]] = [
            {"action": "skipped", "cycle_id": "c1", "cycle_progress": ""},
            {"action": "skipped", "cycle_id": "c1", "cycle_progress": ""},
        ]
        summary = summarize_rows(rows)
        assert summary["total_cycles"] == 1
        assert summary["searched_cycles"] == 0
        assert summary["skip_only_cycles"] == 1

    def test_rows_without_cycle_id_do_not_create_cycle(self) -> None:
        rows: list[dict[str, Any]] = [
            {"action": "info", "cycle_id": None, "cycle_progress": ""},
            {"action": "info", "cycle_id": None, "cycle_progress": ""},
        ]
        summary = summarize_rows(rows)
        assert summary["total_cycles"] == 0


# _partial_validation_error


class TestPartialValidationError:
    def test_returns_422_html(self) -> None:
        resp = _partial_validation_error("instance_id must be an integer")
        assert resp.status_code == 422

    def test_detail_is_html_escaped(self) -> None:
        resp = _partial_validation_error("<script>alert(1)</script>")
        body = resp.body.decode("utf-8")
        assert "&lt;script&gt;" in body
        assert "<script>" not in body

    def test_response_is_feed_shaped(self) -> None:
        """Pin the shape so HTMX swap into #log-feed keeps feed structure."""
        resp = _partial_validation_error("bad input")
        body = resp.body.decode("utf-8")
        assert body.startswith('<div id="log-error-row"')
        assert 'class="empty empty--error"' in body
        assert body.endswith("</div>")
