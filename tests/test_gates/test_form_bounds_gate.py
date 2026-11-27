"""Consolidated invariant: every number field of the instance form is bounded on the server.

The settings form declares ``min`` and ``max`` on its number inputs, but
only the browser reads them, so a crafted or scripted save could store
any integer, including one SQLite cannot hold (#916).  This gate renders
the form, requires every number input to reach ``_validate_form`` as an
integer keyword argument, and checks from a valid baseline that the
server accepts each declared bound, rejects one step past it, and
rejects values outside SQLite's integer range.
"""

from __future__ import annotations

import inspect
from html.parser import HTMLParser

import pytest

from houndarr.routes._templates import get_templates
from houndarr.routes.settings._helpers import blank_instance
from houndarr.services import instance_submit

pytestmark = pytest.mark.pinning


class _NumberInputs(HTMLParser):
    """Collect the attributes of every ``<input type="number">`` by name."""

    def __init__(self) -> None:
        super().__init__()
        self.found: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "input" and fields.get("type") == "number":
            name = fields.get("name")
            assert name, "number input without a name"
            assert name not in self.found, f"duplicate number input {name}"
            self.found[name] = fields


def _render_add_form_inputs() -> dict[str, dict[str, str | None]]:
    blank = blank_instance()
    html = (
        get_templates()
        .env.get_template("partials/instance_form.html")
        .render(instance=blank, defaults=blank, editing=False)
    )
    parser = _NumberInputs()
    parser.feed(html)
    return parser.found


_INPUTS = _render_add_form_inputs()
_PARAMS = inspect.signature(instance_submit._validate_form, eval_str=True).parameters


def _baseline() -> dict[str, object]:
    kwargs: dict[str, object] = {
        name: "" for name, param in _PARAMS.items() if param.annotation is str
    }
    kwargs |= {name: int(str(attrs["value"])) for name, attrs in _INPUTS.items()}
    # An IP literal skips DNS; a hot retry window of 1 makes the interval's minimum apply.
    kwargs |= {
        "url": "http://10.0.0.1:8989",
        "missing_hot_retry_window_hrs": 1,
        "missing_hot_retry_interval_hrs": 2,
    }
    return kwargs


def _accepts(name: str, value: int) -> None:
    instance_submit._validate_form(**{**_baseline(), name: value})  # type: ignore[arg-type]


def _rejects(name: str, value: int) -> None:
    # A settings refusal, so the dialog keeps the tested connection and Save enabled.
    with pytest.raises(instance_submit.InstanceSettingsError):
        instance_submit._validate_form(**{**_baseline(), name: value})  # type: ignore[arg-type]


def test_every_number_input_reaches_the_server_validator() -> None:
    int_params = {name for name, param in _PARAMS.items() if param.annotation is int}
    assert set(_INPUTS) == int_params
    assert len(_INPUTS) == 15


def test_the_baseline_is_valid() -> None:
    instance_submit._validate_form(**_baseline())  # type: ignore[arg-type]


@pytest.mark.parametrize("name", sorted(_INPUTS))
def test_declared_bounds_hold_on_the_server(name: str) -> None:
    attrs = _INPUTS[name]
    assert attrs.get("min") is not None, f"{name} declares no min"
    low = int(str(attrs["min"]))
    _accepts(name, low)
    _rejects(name, low - 1)
    if attrs.get("max") is not None:
        high = int(str(attrs["max"]))
        _accepts(name, high)
        _rejects(name, high + 1)
    _rejects(name, 2**63)
    _rejects(name, -(2**63) - 1)
