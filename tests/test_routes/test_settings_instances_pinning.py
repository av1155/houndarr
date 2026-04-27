"""Pin the pure validation helpers behind /settings/instances.

The ``settings/_helpers.py`` module is the thin request-shaping
layer over :mod:`houndarr.services.instance_validation`.  These
tests lock the exact error messages, return shapes, and branch
logic the 422 paths in ``settings/instances.py`` depend on so
later edits to the service or helper layers cannot drift them
apart.
"""

from __future__ import annotations

import pytest

from houndarr.routes.settings._helpers import (
    ConnectionCheck,
    SearchModes,
    connection_guard_response,
    connection_status_response,
    resolve_search_modes,
    type_mismatch_message,
    validate_cutoff_controls,
    validate_upgrade_controls,
)
from houndarr.services.instances import (
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SonarrSearchMode,
    WhisparrV2SearchMode,
)

pytestmark = pytest.mark.pinning


# validate_cutoff_controls


class TestValidateCutoffControls:
    def test_valid_inputs_return_none(self) -> None:
        assert validate_cutoff_controls(1, 0, 0) is None
        assert validate_cutoff_controls(5, 30, 3) is None

    def test_batch_size_below_one_rejected(self) -> None:
        msg = validate_cutoff_controls(0, 0, 0)
        assert msg == "Cutoff batch size must be at least 1."

    def test_cooldown_days_negative_rejected(self) -> None:
        msg = validate_cutoff_controls(1, -1, 0)
        assert msg == "Cutoff cooldown days must be 0 or greater."

    def test_hourly_cap_negative_rejected(self) -> None:
        msg = validate_cutoff_controls(1, 0, -1)
        assert msg == "Cutoff hourly cap must be 0 or greater."


# validate_upgrade_controls


class TestValidateUpgradeControls:
    def test_valid_inputs_return_none(self) -> None:
        assert validate_upgrade_controls(1, 7, 0) is None
        assert validate_upgrade_controls(3, 90, 2) is None

    def test_batch_size_below_one_rejected(self) -> None:
        msg = validate_upgrade_controls(0, 7, 0)
        assert msg == "Upgrade batch size must be at least 1."

    def test_cooldown_below_seven_rejected(self) -> None:
        msg = validate_upgrade_controls(1, 6, 0)
        assert msg == "Upgrade cooldown days must be at least 7."

    def test_hourly_cap_negative_rejected(self) -> None:
        msg = validate_upgrade_controls(1, 7, -1)
        assert msg == "Upgrade hourly cap must be 0 or greater."


# resolve_search_modes


class TestResolveSearchModes:
    def test_sonarr_type_validates_sonarr_mode(self) -> None:
        """For InstanceType.sonarr the provided sonarr_raw is validated."""
        result = resolve_search_modes(
            InstanceType.sonarr,
            "season_context",
            "album",
            "book",
            "episode",
        )
        assert isinstance(result, SearchModes)
        assert result.sonarr == SonarrSearchMode.season_context

    def test_non_applicable_modes_default_when_type_mismatch(self) -> None:
        """For InstanceType.radarr the non-applicable modes default silently."""
        result = resolve_search_modes(InstanceType.radarr, "bogus", "", "", "")
        assert isinstance(result, SearchModes)
        assert result.sonarr == SonarrSearchMode.episode  # default, not validated
        assert result.lidarr == LidarrSearchMode.album
        assert result.readarr == ReadarrSearchMode.book
        assert result.whisparr_v2 == WhisparrV2SearchMode.episode

    def test_invalid_sonarr_mode_for_sonarr_type(self) -> None:
        result = resolve_search_modes(InstanceType.sonarr, "bogus", "", "", "")
        assert result == "Invalid Sonarr search mode."

    def test_invalid_lidarr_mode_for_lidarr_type(self) -> None:
        result = resolve_search_modes(InstanceType.lidarr, "", "bogus", "", "")
        assert result == "Invalid Lidarr search mode."

    def test_invalid_readarr_mode_for_readarr_type(self) -> None:
        result = resolve_search_modes(InstanceType.readarr, "", "", "bogus", "")
        assert result == "Invalid Readarr search mode."

    def test_invalid_whisparr_v2_mode_for_whisparr_v2(self) -> None:
        result = resolve_search_modes(InstanceType.whisparr_v2, "", "", "", "bogus")
        assert result == "Invalid Whisparr v2 search mode."


# type_mismatch_message


class TestTypeMismatchMessage:
    def test_matching_type_returns_none(self) -> None:
        check = ConnectionCheck(reachable=True, app_name="Sonarr", version="4.0.0")
        assert type_mismatch_message(check, InstanceType.sonarr) is None

    def test_mismatched_type_returns_string(self) -> None:
        check = ConnectionCheck(reachable=True, app_name="Radarr", version="5.0.0")
        msg = type_mismatch_message(check, InstanceType.sonarr)
        assert msg is not None
        assert "Radarr" in msg
        assert "Sonarr" in msg

    def test_unknown_app_name_returns_none(self) -> None:
        """Unknown apps (Readarr fork, etc.) are allowed through."""
        check = ConnectionCheck(reachable=True, app_name="Bookshelf", version="0.1.0")
        assert type_mismatch_message(check, InstanceType.readarr) is None

    def test_whisparr_v3_selected_with_v2_url(self) -> None:
        check = ConnectionCheck(reachable=True, app_name="Whisparr", version="2.0.0")
        msg = type_mismatch_message(check, InstanceType.whisparr_v3)
        assert msg is not None
        assert "Whisparr v2" in msg

    def test_whisparr_v2_selected_with_v3_url(self) -> None:
        check = ConnectionCheck(reachable=True, app_name="Whisparr", version="3.0.0")
        msg = type_mismatch_message(check, InstanceType.whisparr_v2)
        assert msg is not None
        assert "Whisparr v3" in msg

    def test_whisparr_correct_pair_returns_none(self) -> None:
        check = ConnectionCheck(reachable=True, app_name="Whisparr", version="3.0.0")
        assert type_mismatch_message(check, InstanceType.whisparr_v3) is None

    def test_missing_app_name_returns_none(self) -> None:
        check = ConnectionCheck(reachable=True, app_name=None, version=None)
        assert type_mismatch_message(check, InstanceType.sonarr) is None


# connection_status_response + connection_guard_response


class TestConnectionResponses:
    def test_success_status_response_shape(self) -> None:
        resp = connection_status_response("Connected", ok=True, status_code=200)
        assert resp.status_code == 200
        body = resp.body.decode("utf-8")
        assert "text-green-400" in body
        assert "Connected" in body
        assert resp.headers.get("HX-Trigger") == "houndarr-connection-test-success"

    def test_failure_status_response_shape(self) -> None:
        resp = connection_status_response("Failed", ok=False, status_code=422)
        assert resp.status_code == 422
        body = resp.body.decode("utf-8")
        assert "text-red-400" in body
        assert "Failed" in body
        assert resp.headers.get("HX-Trigger") == "houndarr-connection-test-failure"

    def test_status_response_escapes_message(self) -> None:
        resp = connection_status_response("<script>alert(1)</script>", ok=False, status_code=422)
        body = resp.body.decode("utf-8")
        assert "&lt;script&gt;" in body
        assert "<script>" not in body

    def test_guard_response_retargets_and_reswaps(self) -> None:
        resp = connection_guard_response("Verify first")
        assert resp.status_code == 422
        assert resp.headers.get("HX-Retarget") == "#instance-connection-status"
        assert resp.headers.get("HX-Reswap") == "innerHTML"
        assert resp.headers.get("HX-Trigger") == "houndarr-connection-test-failure"
        assert "Verify first" in resp.body.decode("utf-8")

    def test_guard_response_escapes_message(self) -> None:
        resp = connection_guard_response("<b>bold</b>")
        body = resp.body.decode("utf-8")
        assert "&lt;b&gt;bold&lt;/b&gt;" in body
        assert "<b>bold</b>" not in body
