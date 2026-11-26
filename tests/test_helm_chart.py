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


def _render_container(*set_args: str) -> dict[str, Any]:
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
    container: dict[str, Any] = statefulset["spec"]["template"]["spec"]["containers"][0]
    return container


def _render_probes(*set_args: str) -> dict[str, dict[str, Any]]:
    container = _render_container(*set_args)
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
    """Distinct values per field catch a mistyped key or a probe reading another's block."""
    expected = {
        name: {key: 100 + 10 * i + j for j, key in enumerate(fields)}
        for i, (name, fields) in enumerate(_DEFAULT_PROBES.items())
    }
    # An explicit 0 must survive; a sprig `default` fallback would turn it into 5.
    expected["readinessProbe"]["initialDelaySeconds"] = 0
    set_args = [
        f"{name}.{key}={value}"
        for name, fields in expected.items()
        for key, value in fields.items()
    ]

    assert _render_probes(*set_args) == expected


@pytest.mark.parametrize(
    ("set_args", "expected"),
    [
        pytest.param((), None, id="default"),
        pytest.param(("env.urlBase=/houndarr",), "/houndarr", id="set"),
        pytest.param(("env.urlBase=null",), None, id="reuse-values-without-key"),
    ],
)
def test_url_base_sets_env_only_when_configured(
    set_args: tuple[str, ...], expected: str | None
) -> None:
    env = {e["name"]: e.get("value") for e in _render_container(*set_args)["env"]}
    assert env.get("HOUNDARR_URL_BASE") == expected


def test_chart_passes_strict_lint() -> None:
    result = subprocess.run(
        ["helm", "lint", "--strict", str(_CHART)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
