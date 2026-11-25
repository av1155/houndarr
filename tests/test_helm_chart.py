"""Helm chart probe rendering (#830).

Kubernetes gives a probe 1 s when ``timeoutSeconds`` is unset, while the Docker
HEALTHCHECK allows 10 s, so the chart sets every probe's timings explicitly.
``helm upgrade --reuse-values`` keeps the previous chart's values, so the
template also has to render when a probe block or key is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_CHART = Path(__file__).resolve().parents[1] / "charts" / "houndarr"

# In CI a missing helm fails these tests instead of skipping them.
pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None and not os.environ.get("CI"),
    reason="helm is not installed",
)

_DEFAULT_PROBES: dict[str, dict[str, int]] = {
    "startupProbe": {"failureThreshold": 60, "periodSeconds": 10, "timeoutSeconds": 5},
    "livenessProbe": {
        "initialDelaySeconds": 10,
        "periodSeconds": 30,
        "timeoutSeconds": 5,
        "failureThreshold": 3,
    },
    "readinessProbe": {
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "timeoutSeconds": 5,
        "failureThreshold": 3,
    },
}


def _render_probes(*set_args: str) -> dict[str, dict[str, Any]]:
    cmd = ["helm", "template", "houndarr", str(_CHART)]
    for arg in set_args:
        cmd += ["--set", arg]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    statefulset = next(
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc is not None and doc["kind"] == "StatefulSet"
    )
    container = statefulset["spec"]["template"]["spec"]["containers"][0]
    probes: dict[str, dict[str, Any]] = {}
    for name in _DEFAULT_PROBES:
        probe = dict(container[name])
        assert probe.pop("httpGet") == {"path": "/api/health", "port": "http"}
        probes[name] = probe
    return probes


def test_default_render_sets_every_probe_timeout() -> None:
    assert _render_probes() == _DEFAULT_PROBES


def test_values_yaml_lists_every_probe_default() -> None:
    """The template fallbacks would hide a block dropped from ``helm show values``."""
    values = yaml.safe_load((_CHART / "values.yaml").read_text())
    assert {name: values[name] for name in _DEFAULT_PROBES} == _DEFAULT_PROBES


@pytest.mark.parametrize(
    "set_args",
    [
        pytest.param(
            (
                "startupProbe.timeoutSeconds=null",
                "livenessProbe=null",
                "readinessProbe=null",
            ),
            id="chart-1.13.3-values",
        ),
        pytest.param(
            ("startupProbe=null", "livenessProbe=null", "readinessProbe=null"),
            id="no-probe-values",
        ),
    ],
)
def test_missing_probe_values_fall_back_to_the_values_yaml_defaults(
    set_args: tuple[str, ...],
) -> None:
    assert _render_probes(*set_args) == _DEFAULT_PROBES


def test_overrides_reach_the_rendered_probes() -> None:
    probes = _render_probes(
        "startupProbe.failureThreshold=120",
        "livenessProbe.timeoutSeconds=10",
        "readinessProbe.initialDelaySeconds=0",
        "readinessProbe.failureThreshold=6",
    )

    expected = {name: dict(fields) for name, fields in _DEFAULT_PROBES.items()}
    expected["startupProbe"]["failureThreshold"] = 120
    expected["livenessProbe"]["timeoutSeconds"] = 10
    expected["readinessProbe"]["initialDelaySeconds"] = 0
    expected["readinessProbe"]["failureThreshold"] = 6
    assert probes == expected


def test_chart_passes_strict_lint() -> None:
    result = subprocess.run(
        ["helm", "lint", "--strict", str(_CHART)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
