"""Pinning tests for services.instance_validation.run_connection_test.

:func:`houndarr.services.instance_validation.run_connection_test`
owns the full test-connection orchestration.  The route is a thin
dispatch: it calls the service, gets a :class:`ConnectionTestOutcome`,
and feeds the three fields straight into
:func:`connection_status_response`.

These tests lock the per-branch behaviour of the service so later
edits cannot silently drift it.  Every branch of the orchestration
has one test:

* Invalid type string -> 422, "Invalid instance type."
* URL that fails the SSRF gate -> 422, URL-validation message.
* ``__UNCHANGED__`` sentinel with a non-numeric instance_id -> 422,
  "Invalid instance ID for key lookup."
* Sentinel with an instance_id that does not exist -> 404,
  "Instance not found."
* Sentinel with an existing instance_id -> uses the stored api_key
  on the probe (verified by asserting the bytes passed into the
  monkey-patched check_connection).
* Reachable=False from the probe -> 422, "Connection failed. ..."
* Type mismatch (app_name disagrees) -> 422, mismatch message.
* Success without instance_id (add flow) -> 200, "add this instance".
* Success with instance_id (edit flow) -> 200, "save changes".
* Success without app_name -> 200, generic success message.
* Success with app_name but no version -> 200, no-version message.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

from houndarr.services.instance_validation import (
    API_KEY_UNCHANGED,
    ConnectionCheck,
    ConnectionTestOutcome,
    run_connection_test,
)
from houndarr.services.instances import InstanceType, create_instance

pytestmark = pytest.mark.pinning


@pytest.fixture()
def master_key() -> bytes:
    """Fresh Fernet key per test."""
    return Fernet.generate_key()


@pytest.mark.asyncio()
async def test_invalid_type_returns_422() -> None:
    """Unknown type strings produce the validation error."""
    outcome = await run_connection_test(
        master_key=b"",
        type_value="not-a-type",
        url="http://sonarr:8989",
        api_key="key",
    )
    assert outcome == ConnectionTestOutcome(
        ok=False, message="Invalid instance type.", status_code=422
    )


@pytest.mark.asyncio()
async def test_ssrf_gate_blocks_loopback_literal() -> None:
    """The SSRF validator's message bubbles through unchanged."""
    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://169.254.169.254/latest/",
        api_key="key",
    )
    assert outcome.ok is False
    assert outcome.status_code == 422
    # The message is the validator's; it is specific to the reason
    # the URL is rejected.  We assert on status_code + ok flag rather
    # than on the exact wording to stay decoupled from the validator.


@pytest.mark.asyncio()
async def test_sentinel_without_instance_id_returns_422() -> None:
    """Sentinel api_key with no instance_id produces the 'Provide an API key.' error.

    The add-instance form never submits the sentinel; the edit form
    always populates instance_id.  A hand-crafted POST that sends
    ``api_key=__UNCHANGED__`` with no instance_id used to fall
    through and probe the remote with the literal sentinel string,
    producing a generic "Connection failed" message.  The guard
    surfaces the specific error instead.
    """
    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key=API_KEY_UNCHANGED,
        instance_id="",
    )
    assert outcome == ConnectionTestOutcome(
        ok=False, message="Provide an API key.", status_code=422
    )


@pytest.mark.asyncio()
async def test_sentinel_with_non_numeric_instance_id_returns_422() -> None:
    """Sentinel path with a malformed instance_id fails fast."""
    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key=API_KEY_UNCHANGED,
        instance_id="not-a-number",
    )
    assert outcome == ConnectionTestOutcome(
        ok=False,
        message="Invalid instance ID for key lookup.",
        status_code=422,
    )


@pytest.mark.asyncio()
async def test_sentinel_with_unknown_instance_id_returns_404(db: None, master_key: bytes) -> None:
    """Sentinel path with an instance_id that does not exist returns 404."""
    outcome = await run_connection_test(
        master_key=master_key,
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key=API_KEY_UNCHANGED,
        instance_id="9999",
    )
    assert outcome == ConnectionTestOutcome(
        ok=False, message="Instance not found.", status_code=404
    )


@pytest.mark.asyncio()
async def test_sentinel_resolves_stored_api_key(
    db: None, master_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sentinel path pulls the stored decrypted api_key for the probe."""
    inst = await create_instance(
        master_key=master_key,
        name="Sonarr",
        type=InstanceType.sonarr,
        url="http://sonarr:8989",
        api_key="stored-secret",
    )

    captured: dict[str, Any] = {}

    async def fake_check_connection(instance_type: Any, url: str, api_key: str) -> ConnectionCheck:
        captured["api_key"] = api_key
        return ConnectionCheck(reachable=True, app_name="Sonarr", version="4.0.0")

    monkeypatch.setattr(
        "houndarr.services.instance_validation.check_connection", fake_check_connection
    )

    outcome = await run_connection_test(
        master_key=master_key,
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key=API_KEY_UNCHANGED,
        instance_id=str(inst.core.id),
    )
    assert outcome.ok is True
    assert outcome.status_code == 200
    assert captured["api_key"] == "stored-secret"


@pytest.mark.asyncio()
async def test_unreachable_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """reachable=False from the probe renders the canonical failure message."""

    async def fake_check_connection(instance_type: Any, url: str, api_key: str) -> ConnectionCheck:
        return ConnectionCheck(reachable=False)

    monkeypatch.setattr(
        "houndarr.services.instance_validation.check_connection", fake_check_connection
    )

    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key="key",
    )
    assert outcome == ConnectionTestOutcome(
        ok=False,
        message="Connection failed. Check URL/API key and try again.",
        status_code=422,
    )


@pytest.mark.asyncio()
async def test_type_mismatch_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """An app_name that contradicts the selected type surfaces the mismatch."""

    async def fake_check_connection(instance_type: Any, url: str, api_key: str) -> ConnectionCheck:
        return ConnectionCheck(reachable=True, app_name="Radarr", version="5.0.0")

    monkeypatch.setattr(
        "houndarr.services.instance_validation.check_connection", fake_check_connection
    )

    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key="key",
    )
    assert outcome.ok is False
    assert outcome.status_code == 422
    # The mismatch text comes from type_mismatch_message; assert its
    # core shape.
    assert "Radarr" in outcome.message
    assert "Sonarr" in outcome.message


@pytest.mark.asyncio()
async def test_success_add_flow_uses_add_phrasing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful probe without instance_id renders the add-phrasing."""

    async def fake_check_connection(instance_type: Any, url: str, api_key: str) -> ConnectionCheck:
        return ConnectionCheck(reachable=True, app_name="Sonarr", version="4.0.0")

    monkeypatch.setattr(
        "houndarr.services.instance_validation.check_connection", fake_check_connection
    )

    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key="key",
    )
    assert outcome.ok is True
    assert outcome.status_code == 200
    assert outcome.message == "Connected to Sonarr v4.0.0. You can now add this instance."


@pytest.mark.asyncio()
async def test_success_edit_flow_uses_save_phrasing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful probe with instance_id set renders the save-phrasing."""

    async def fake_check_connection(instance_type: Any, url: str, api_key: str) -> ConnectionCheck:
        return ConnectionCheck(reachable=True, app_name="Sonarr", version="4.0.0")

    monkeypatch.setattr(
        "houndarr.services.instance_validation.check_connection", fake_check_connection
    )

    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key="key",
        instance_id="42",
    )
    assert outcome.ok is True
    assert outcome.message == "Connected to Sonarr v4.0.0. You can now save changes."


@pytest.mark.asyncio()
async def test_success_without_app_name_uses_generic_phrasing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable probe that does not report app_name still succeeds."""

    async def fake_check_connection(instance_type: Any, url: str, api_key: str) -> ConnectionCheck:
        return ConnectionCheck(reachable=True)

    monkeypatch.setattr(
        "houndarr.services.instance_validation.check_connection", fake_check_connection
    )

    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key="key",
    )
    assert outcome.ok is True
    assert outcome.message == "Connection successful. You can now add this instance."


@pytest.mark.asyncio()
async def test_success_with_app_name_no_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that reports app_name without version uses the no-version message."""

    async def fake_check_connection(instance_type: Any, url: str, api_key: str) -> ConnectionCheck:
        return ConnectionCheck(reachable=True, app_name="Sonarr", version=None)

    monkeypatch.setattr(
        "houndarr.services.instance_validation.check_connection", fake_check_connection
    )

    outcome = await run_connection_test(
        master_key=b"",
        type_value="sonarr",
        url="http://sonarr:8989",
        api_key="key",
    )
    assert outcome.ok is True
    assert outcome.message == "Connected to Sonarr. You can now add this instance."


def test_connection_test_outcome_is_frozen_slotted() -> None:
    """ConnectionTestOutcome stays frozen + slotted for the slots audit."""
    import dataclasses

    assert dataclasses.is_dataclass(ConnectionTestOutcome)
    params = ConnectionTestOutcome.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen is True
    assert "__slots__" in ConnectionTestOutcome.__dict__
