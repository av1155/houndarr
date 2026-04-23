"""Characterisation pins for the Instance dataclass immutability contract.

Two invariants are locked here:

1. ``test_instance_is_frozen_after_freeze`` asserts that
   :class:`Instance` is ``@dataclass(frozen=True, slots=True)`` and
   that per-attribute assignment raises ``FrozenInstanceError``.
2. ``test_offset_advancement_persisted_via_repository_only`` walks
   the production tree and asserts no source file writes to
   ``instance.schedule.*`` or ``instance.upgrade.*``.  Any such
   write would be silently shadowed by the next ``get_instance``
   and would turn into ``FrozenInstanceError`` at runtime; catching
   it here keeps the repository as the single write path.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from houndarr.services.instances import (
    CutoffPolicy,
    Instance,
    InstanceCore,
    InstanceTimestamps,
    InstanceType,
    MissingPolicy,
    RuntimeSnapshot,
    SchedulePolicy,
    UpgradePolicy,
)

pytestmark = pytest.mark.pinning

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "houndarr"


def _make_instance() -> Instance:
    """Return a minimally-populated Instance for mutation tests."""
    return Instance(
        core=InstanceCore(
            id=1,
            name="pinning",
            type=InstanceType.sonarr,
            url="http://sonarr:8989",
            api_key="plaintext",
        ),
        missing=MissingPolicy(),
        cutoff=CutoffPolicy(),
        upgrade=UpgradePolicy(),
        schedule=SchedulePolicy(),
        snapshot=RuntimeSnapshot(),
        timestamps=InstanceTimestamps(
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
    )


def test_instance_is_frozen_after_freeze() -> None:
    """Instance is ``@dataclass(frozen=True, slots=True)``.

    Any per-attribute assignment must raise
    ``FrozenInstanceError`` and the seven sub-struct names must
    appear in ``__slots__``.  Callers that need a modified Instance
    compose :func:`dataclasses.replace`.
    """
    assert dataclasses.is_dataclass(Instance)
    assert Instance.__dataclass_params__.frozen is True
    assert set(Instance.__slots__) == {
        "core",
        "missing",
        "cutoff",
        "upgrade",
        "schedule",
        "snapshot",
        "timestamps",
    }

    instance = _make_instance()
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.missing = MissingPolicy()  # type: ignore[misc]

    # ``dataclasses.replace`` is the supported evolution path.
    rebuilt = dataclasses.replace(
        instance, missing=dataclasses.replace(instance.missing, batch_size=99)
    )
    assert rebuilt.missing.batch_size == 99


_FORBIDDEN_ATTR_PREFIXES: tuple[str, ...] = (
    "schedule",
    "upgrade",
)

_FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "missing_page_offset",
        "cutoff_page_offset",
        "upgrade_item_offset",
        "upgrade_series_offset",
    }
)


class _ForbiddenWriteVisitor(ast.NodeVisitor):
    """Flag any `instance.<schedule|upgrade>.<field> =` assignment."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._check_target(target, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_target(node.target, node)
        self.generic_visit(node)

    def _check_target(self, target: ast.AST, node: ast.AST) -> None:
        if not isinstance(target, ast.Attribute):
            return
        # Match foo.schedule.<bar> or foo.upgrade.<bar>.
        if (
            isinstance(target.value, ast.Attribute)
            and target.value.attr in _FORBIDDEN_ATTR_PREFIXES
        ):
            self.findings.append((node.lineno, target.attr))
            return
        # Match foo.missing_page_offset etc. (flat writes through the facade).
        if target.attr in _FORBIDDEN_ATTRS:
            self.findings.append((node.lineno, target.attr))


def test_offset_advancement_persisted_via_repository_only() -> None:
    """No production source may write to Instance offset sub-struct fields.

    Engine and adapter code must advance offsets exclusively through
    the repository ``update_instance`` payload.  A production-side
    write would survive a round trip only until the next
    ``get_instance``, silently shadowing the repository's
    authoritative value, and would also raise
    ``FrozenInstanceError`` at runtime against the frozen facade.
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in _SRC_ROOT.rglob("*.py"):
        # Skip the services/instances.py module itself (owns the
        # dataclass and its docstring example).  Skip the repositories
        # tree (the legitimate write boundary operates on repository
        # payloads, not Instance attributes).
        rel = path.relative_to(_SRC_ROOT)
        if rel == Path("services/instances.py"):
            continue
        if rel.parts and rel.parts[0] == "repositories":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        visitor = _ForbiddenWriteVisitor()
        visitor.visit(tree)
        offenders.extend((path, lineno, attr) for lineno, attr in visitor.findings)

    assert offenders == [], (
        "production writes to Instance offset fields must go through the "
        f"repository; found {offenders!r}"
    )
