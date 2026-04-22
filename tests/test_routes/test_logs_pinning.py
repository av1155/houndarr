"""Pin the pure helpers in routes/api/logs.py.

Track A.14 of the refactor plan.  Track D.9 will extract the dynamic SQL
builder into ``services/log_query.py``.  These tests lock the parser
helpers (_parse_instance_id / _parse_search_kind / _parse_cycle_trigger
/ _parse_hide_system), the summary builder (_summarize_rows), the
limit clamp (_compute_load_more_limit), and the HTMX 422 partial shape
(_partial_validation_error) so the extraction cannot drift them.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from houndarr.routes.api.logs import (
    _compute_load_more_limit,
    _parse_cycle_trigger,
    _parse_hide_system,
    _parse_instance_id,
    _parse_search_kind,
    _partial_validation_error,
    _summarize_rows,
)

pytestmark = pytest.mark.pinning


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


class TestParseInstanceId:
    def test_none_returns_none(self) -> None:
        assert _parse_instance_id(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_instance_id("") is None

    def test_integer_string_parses(self) -> None:
        assert _parse_instance_id("42") == 42

    def test_negative_accepted(self) -> None:
        """Pinning quirk: negative ints pass the int() cast; upstream should gate."""
        assert _parse_instance_id("-1") == -1


class TestParseSearchKind:
    @pytest.mark.parametrize("kind", ["missing", "cutoff", "upgrade"])
    def test_known_kinds_accepted(self, kind: str) -> None:
        assert _parse_search_kind(kind) == kind

    def test_none_returns_none(self) -> None:
        assert _parse_search_kind(None) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_search_kind("") is None

    def test_unknown_kind_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _parse_search_kind("bogus")
        assert exc.value.status_code == 422


class TestParseCycleTrigger:
    @pytest.mark.parametrize("trigger", ["scheduled", "run_now", "system"])
    def test_known_triggers_accepted(self, trigger: str) -> None:
        assert _parse_cycle_trigger(trigger) == trigger

    def test_none_returns_none(self) -> None:
        assert _parse_cycle_trigger(None) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_cycle_trigger("") is None

    def test_unknown_trigger_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _parse_cycle_trigger("manual")
        assert exc.value.status_code == 422


class TestParseHideSystem:
    @pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE", "yes", "on", " On "])
    def test_truthy_values(self, raw: str) -> None:
        assert _parse_hide_system(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "off"])
    def test_falsy_values(self, raw: str) -> None:
        assert _parse_hide_system(raw) is False

    def test_none_returns_false(self) -> None:
        assert _parse_hide_system(None) is False

    def test_empty_returns_false(self) -> None:
        assert _parse_hide_system("") is False

    def test_garbage_raises_422(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _parse_hide_system("maybe")
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# _compute_load_more_limit
# ---------------------------------------------------------------------------


class TestComputeLoadMoreLimit:
    def test_small_limit_capped_at_100_upper(self) -> None:
        assert _compute_load_more_limit(50) == 50

    def test_at_100_returns_100(self) -> None:
        assert _compute_load_more_limit(100) == 100

    def test_over_100_capped_to_100(self) -> None:
        assert _compute_load_more_limit(500) == 100

    def test_zero_clamped_to_one(self) -> None:
        """Minimum is 1 even if caller passes 0 or negative."""
        assert _compute_load_more_limit(0) == 1
        assert _compute_load_more_limit(-50) == 1


# ---------------------------------------------------------------------------
# _summarize_rows
# ---------------------------------------------------------------------------


class TestSummarizeRows:
    def test_empty_rows_yields_zero_everything(self) -> None:
        summary = _summarize_rows([])
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
        summary = _summarize_rows(rows)
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
        summary = _summarize_rows(rows)
        assert summary["total_cycles"] == 1
        assert summary["searched_cycles"] == 1
        assert summary["skip_only_cycles"] == 0

    def test_cycle_with_only_skips_is_skip_only(self) -> None:
        rows: list[dict[str, Any]] = [
            {"action": "skipped", "cycle_id": "c1", "cycle_progress": ""},
            {"action": "skipped", "cycle_id": "c1", "cycle_progress": ""},
        ]
        summary = _summarize_rows(rows)
        assert summary["total_cycles"] == 1
        assert summary["searched_cycles"] == 0
        assert summary["skip_only_cycles"] == 1

    def test_rows_without_cycle_id_do_not_create_cycle(self) -> None:
        rows: list[dict[str, Any]] = [
            {"action": "info", "cycle_id": None, "cycle_progress": ""},
            {"action": "info", "cycle_id": None, "cycle_progress": ""},
        ]
        summary = _summarize_rows(rows)
        assert summary["total_cycles"] == 0


# ---------------------------------------------------------------------------
# _partial_validation_error
# ---------------------------------------------------------------------------


class TestPartialValidationError:
    def test_returns_422_html(self) -> None:
        resp = _partial_validation_error("instance_id must be an integer")
        assert resp.status_code == 422

    def test_detail_is_html_escaped(self) -> None:
        resp = _partial_validation_error("<script>alert(1)</script>")
        body = resp.body.decode("utf-8")
        assert "&lt;script&gt;" in body
        assert "<script>" not in body

    def test_response_is_tr_tbody_compatible(self) -> None:
        """Pin the shape so HTMX swap into #log-tbody keeps table structure."""
        resp = _partial_validation_error("bad input")
        body = resp.body.decode("utf-8")
        assert body.startswith('<tr id="log-error-row">')
        assert 'colspan="10"' in body
        assert body.endswith("</tr>")
