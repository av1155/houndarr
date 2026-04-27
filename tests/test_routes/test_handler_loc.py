"""Route-handler LOC cap.

Walks every file under ``src/houndarr/routes/``, finds every
function decorated with ``@router.<verb>(...)``, and asserts the
body LOC stays under a 200-line soft cap.  Crossing the cap
signals that a handler should lift logic into a service instead of
growing further.

Body LOC here means the count of source lines inside the ``def
...`` block, excluding the leading docstring and trailing blank
tail.  Decorator lines, the signature, and closing parens of
multi-line argument lists are not counted.

If this test fails, either the handler genuinely needs a service
extraction or the cap needs lifting; lifting the cap requires a
commit message explaining why.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.pinning


ROUTES_DIR = Path(__file__).resolve().parents[2] / "src" / "houndarr" / "routes"
SOFT_CAP = 200


# Non-handler route-module files.  They may live under routes/ but they
# do not declare ``@router.<verb>`` endpoints.
_NON_HANDLER_MODULES = {
    "__init__.py",
    "_htmx.py",
    "_templates.py",
}


def _is_router_decorated(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Return True when *node* carries a ``@router.<verb>(...)`` decorator."""
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id == "router":
            return True
    return False


def _body_loc(node: ast.AsyncFunctionDef | ast.FunctionDef) -> int:
    """Return the non-docstring body line count for *node*."""
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return 0
    start = body[0].lineno
    end = body[-1].end_lineno or body[-1].lineno
    return end - start + 1


def _walk_handlers() -> list[tuple[str, str, int]]:
    """Return (relative_file, handler_name, body_loc) for every route."""
    handlers: list[tuple[str, str, int]] = []
    for py in sorted(ROUTES_DIR.rglob("*.py")):
        if py.name in _NON_HANDLER_MODULES:
            continue
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if not _is_router_decorated(node):
                continue
            rel = py.relative_to(ROUTES_DIR.parent).as_posix()
            handlers.append((rel, node.name, _body_loc(node)))
    return handlers


def test_handler_count_matches_audit_snapshot() -> None:
    """The handler inventory count matches the current snapshot (33 handlers).

    A regression here means a handler was added or removed without
    an accompanying update to this count.  Update this count
    together with the route change so the audit stays honest.
    """
    assert len(_walk_handlers()) == 33


def test_every_handler_under_soft_cap() -> None:
    """No route handler body exceeds the 200-line soft cap."""
    handlers = _walk_handlers()
    over = [(f, n, loc) for f, n, loc in handlers if loc > SOFT_CAP]
    assert over == [], "handlers over the 200-line soft cap: " + ", ".join(
        f"{f}:{n} = {loc}" for f, n, loc in over
    )


def test_audit_max_handler_is_admin_factory_reset() -> None:
    """The largest handler in the audit snapshot is admin_factory_reset.

    Locks the current outlier so a surprise bump above it surfaces
    as a test failure with the specific handler named.  Lifting this
    invariant is legitimate (new complex handler) but requires an
    explicit snapshot refresh.
    """
    handlers = _walk_handlers()
    top = max(handlers, key=lambda h: h[2])
    assert top[1] == "admin_factory_reset"
    # Current snapshot: 95 body LOC.  Drift by more than 20 lines
    # either way flags a meaningful behaviour change the audit
    # doc should pick up.
    assert 75 <= top[2] <= 115
