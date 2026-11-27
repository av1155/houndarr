"""Consolidated invariant: the instance form's type-gated controls follow the Type select.

The form renders each type-gated control shown or hidden for the
instance's own type, and ``syncAppOnlyControls`` in
``static/js/settings.js`` re-applies that on every Type change, modal
swap and Reset to Defaults from its attribute -> types map.  A gating
attribute the map does not list keeps its server render whatever type
the user picks, which is how the Upgrade Series Window stayed hidden
on Sonarr in the Add form (#907).  Each case renders the Edit form for
one type and requires every gated control's server visibility to equal
what the map gives for that type.
"""

from __future__ import annotations

import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest

from houndarr.routes._templates import get_templates
from houndarr.routes.settings._helpers import blank_instance
from houndarr.services.instances import InstanceType

pytestmark = pytest.mark.pinning

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS_JS = _REPO_ROOT / "src" / "houndarr" / "static" / "js" / "settings.js"
_GATE_ATTR = re.compile(r"^data-[a-z0-9_-]+-only$")


def _js_type_map() -> dict[str, frozenset[str]]:
    source = _SETTINGS_JS.read_text(encoding="utf-8")
    block = re.search(r"const APP_ONLY_TYPES = \{(.*?)\};", source, re.DOTALL)
    assert block, "settings.js no longer declares the APP_ONLY_TYPES map"
    entries = re.findall(r"'(data-[a-z0-9_-]+-only)':\s*\[([^\]]*)\]", block.group(1))
    return {attr: frozenset(re.findall(r"'([a-z0-9_]+)'", types)) for attr, types in entries}


class _GatedControls(HTMLParser):
    """Collect (attribute, shown) for every element carrying a gating attribute."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        style = dict(attrs).get("style") or ""
        shown = "display:none" not in style.replace(" ", "")
        self.found.extend((name, shown) for name, value in attrs if _GATE_ATTR.match(name))


def _render_edit_form(instance_type: InstanceType) -> list[tuple[str, bool]]:
    blank = blank_instance()
    instance = replace(blank, core=replace(blank.core, id=1, type=instance_type))
    html = (
        get_templates()
        .env.get_template("partials/instance_form.html")
        .render(instance=instance, defaults=blank, editing=True)
    )
    parser = _GatedControls()
    parser.feed(html)
    return parser.found


def test_map_names_only_real_instance_types() -> None:
    valid = {t.value for t in InstanceType}
    for attr, types in _js_type_map().items():
        assert types, f"{attr} maps to no instance type"
        assert types <= valid, f"{attr} maps to unknown types {sorted(types - valid)}"


@pytest.mark.parametrize("instance_type", list(InstanceType), ids=lambda t: t.value)
def test_server_render_matches_js_map(instance_type: InstanceType) -> None:
    type_map = _js_type_map()
    controls = _render_edit_form(instance_type)
    assert controls, "the instance form has no type-gated controls"
    for attr, shown in controls:
        assert attr in type_map, f"settings.js APP_ONLY_TYPES does not handle {attr}"
        expected = instance_type.value in type_map[attr]
        assert shown == expected, (
            f"{attr} renders {'shown' if shown else 'hidden'} for {instance_type.value}, "
            f"but settings.js would {'show' if expected else 'hide'} it"
        )
