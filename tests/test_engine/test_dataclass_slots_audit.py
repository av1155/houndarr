"""Regression test: every frozen dataclass under ``src/houndarr`` uses slots.

AGENTS.md records the convention: every frozen dataclass in the
codebase uses ``slots=True`` for the small memory win and to make
attribute typos surface as ``AttributeError`` rather than silently
writing a new instance attribute.

Two dataclasses are deliberately mutable and unslotted:

- :class:`houndarr.config.AppSettings` (env overrides applied
    in-place).
- :class:`houndarr.engine.retry.ReconnectState` (one-field state
    flipped by the supervisor's reconnect helper).

This test walks every module under ``src/houndarr``, finds every
dataclass, and asserts that frozen ones declare ``__slots__``.  A
new frozen dataclass added without ``slots=True`` will fail this
test on the next run.
"""

from __future__ import annotations

import dataclasses
import importlib
import pkgutil
from types import ModuleType

import pytest

import houndarr

pytestmark = pytest.mark.pinning


def _walk_modules(pkg: ModuleType) -> list[ModuleType]:
    """Import every submodule of *pkg* and return the resulting list."""
    modules: list[ModuleType] = [pkg]
    if not hasattr(pkg, "__path__"):
        return modules
    for module_info in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        modules.append(importlib.import_module(module_info.name))
    return modules


def _collect_frozen_dataclasses() -> list[type]:
    """Return every frozen dataclass declared in the houndarr package."""
    found: list[type] = []
    for module in _walk_modules(houndarr):
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if not isinstance(attr, type):
                continue
            if attr.__module__ != module.__name__:
                # Re-exported from another module; only audit at the
                # declaration site to avoid double-reporting.
                continue
            if not dataclasses.is_dataclass(attr):
                continue
            params = getattr(attr, "__dataclass_params__", None)
            if params is None or not params.frozen:
                continue
            found.append(attr)
    return found


def test_every_frozen_dataclass_uses_slots() -> None:
    """Every frozen dataclass in src/houndarr declares ``__slots__``."""
    offenders: list[str] = []
    for cls in _collect_frozen_dataclasses():
        # ``__slots__`` exists on the class iff slots=True was passed
        # to @dataclass; the convention is loud and explicit.
        if "__slots__" not in cls.__dict__:
            offenders.append(f"{cls.__module__}.{cls.__qualname__}")
    assert offenders == [], "frozen dataclasses missing slots=True: " + ", ".join(sorted(offenders))


def test_audit_finds_at_least_the_known_frozen_set() -> None:
    """Sanity check: the audit reaches the per-client domain dataclasses.

    Guards against the walker silently skipping the module tree (e.g.
    if pkgutil.walk_packages stopped recursing on namespace packages).
    """
    names = {f"{cls.__module__}.{cls.__qualname__}" for cls in _collect_frozen_dataclasses()}
    expected_subset = {
        "houndarr.engine.candidates.SearchCandidate",
        "houndarr.value_objects.ItemRef",
        "houndarr.engine.config.search_pass.SearchPassConfig",
        "houndarr.engine.adapters._common.ContextOverride",
        "houndarr.clients.base.InstanceSnapshot",
    }
    missing = expected_subset - names
    assert missing == set(), f"audit walker missed expected dataclasses: {sorted(missing)}"
