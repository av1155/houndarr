"""Pin the header dict each `_htmx.py` response helper emits.

One-line helpers in :mod:`houndarr.routes._htmx` own the
``HX-Refresh / HX-Redirect / HX-Trigger /
HX-Trigger-After-Swap / HX-Retarget / HX-Reswap`` wire names.
``test_hx_headers_pinning.py`` pins each consumer route end-to-end;
these tests pin the helpers in isolation so a typo in a header
name, a renamed kwarg, or a dropped optional branch surfaces
without having to spin up the full app.

The helpers mutate the response in place and return the same
instance (TypeVar-preserved), so each test constructs a fresh
``Response`` subclass, feeds it through the helper, and inspects
the resulting ``MutableHeaders``.
"""

from __future__ import annotations

import pytest
from fastapi.responses import HTMLResponse, Response

from houndarr.routes._htmx import (
    hx_redirect_response,
    hx_refresh_response,
    hx_retarget_response,
    hx_trigger_after_swap,
    hx_trigger_response,
)

pytestmark = pytest.mark.pinning


class TestHxRefreshResponse:
    def test_sets_hx_refresh_true(self) -> None:
        resp = hx_refresh_response(Response(status_code=200))
        assert resp.headers["HX-Refresh"] == "true"

    def test_returns_same_instance(self) -> None:
        original = Response(status_code=200)
        returned = hx_refresh_response(original)
        assert returned is original

    def test_preserves_status_code(self) -> None:
        resp = hx_refresh_response(Response(status_code=204))
        assert resp.status_code == 204

    def test_preserves_concrete_subtype(self) -> None:
        resp = hx_refresh_response(HTMLResponse(content="x", status_code=200))
        assert isinstance(resp, HTMLResponse)

    def test_no_other_hx_headers_set(self) -> None:
        resp = hx_refresh_response(Response(status_code=200))
        assert "HX-Redirect" not in resp.headers
        assert "HX-Trigger" not in resp.headers


class TestHxRedirectResponse:
    def test_sets_hx_redirect_to_location(self) -> None:
        resp = hx_redirect_response(Response(status_code=200), "/setup")
        assert resp.headers["HX-Redirect"] == "/setup"

    def test_root_location(self) -> None:
        resp = hx_redirect_response(Response(status_code=200), "/")
        assert resp.headers["HX-Redirect"] == "/"

    def test_query_and_fragment_preserved(self) -> None:
        resp = hx_redirect_response(
            Response(status_code=200),
            "/settings?tab=account#password",
        )
        assert resp.headers["HX-Redirect"] == "/settings?tab=account#password"

    def test_returns_same_instance(self) -> None:
        original = Response(status_code=200)
        returned = hx_redirect_response(original, "/setup")
        assert returned is original

    def test_no_hx_refresh(self) -> None:
        resp = hx_redirect_response(Response(status_code=200), "/setup")
        assert "HX-Refresh" not in resp.headers


class TestHxTriggerResponse:
    def test_sets_hx_trigger_event_name(self) -> None:
        resp = hx_trigger_response(HTMLResponse(content="x"), "houndarr-demo")
        assert resp.headers["HX-Trigger"] == "houndarr-demo"

    def test_connection_test_success_event(self) -> None:
        resp = hx_trigger_response(
            HTMLResponse(content="x"),
            "houndarr-connection-test-success",
        )
        assert resp.headers["HX-Trigger"] == "houndarr-connection-test-success"

    def test_connection_test_failure_event(self) -> None:
        resp = hx_trigger_response(
            HTMLResponse(content="x"),
            "houndarr-connection-test-failure",
        )
        assert resp.headers["HX-Trigger"] == "houndarr-connection-test-failure"

    def test_returns_same_instance(self) -> None:
        original = HTMLResponse(content="x")
        returned = hx_trigger_response(original, "ev")
        assert returned is original

    def test_does_not_set_after_swap_variant(self) -> None:
        resp = hx_trigger_response(HTMLResponse(content="x"), "ev")
        assert "HX-Trigger-After-Swap" not in resp.headers


class TestHxTriggerAfterSwap:
    def test_sets_hx_trigger_after_swap_event(self) -> None:
        resp = hx_trigger_after_swap(
            HTMLResponse(content="x"),
            "houndarr-show-changelog",
        )
        assert resp.headers["HX-Trigger-After-Swap"] == "houndarr-show-changelog"

    def test_does_not_set_plain_trigger(self) -> None:
        resp = hx_trigger_after_swap(HTMLResponse(content="x"), "ev")
        assert "HX-Trigger" not in resp.headers

    def test_returns_same_instance(self) -> None:
        original = HTMLResponse(content="x")
        returned = hx_trigger_after_swap(original, "ev")
        assert returned is original

    def test_preserves_concrete_subtype(self) -> None:
        resp = hx_trigger_after_swap(HTMLResponse(content="x"), "ev")
        assert isinstance(resp, HTMLResponse)


class TestHxRetargetResponse:
    def test_sets_retarget_and_reswap(self) -> None:
        resp = hx_retarget_response(
            HTMLResponse(content="x", status_code=422),
            target="#instance-connection-status",
            reswap="innerHTML",
        )
        assert resp.headers["HX-Retarget"] == "#instance-connection-status"
        assert resp.headers["HX-Reswap"] == "innerHTML"

    def test_optional_trigger_set_when_provided(self) -> None:
        resp = hx_retarget_response(
            HTMLResponse(content="x", status_code=422),
            target="#slot",
            reswap="innerHTML",
            trigger="houndarr-connection-test-failure",
        )
        assert resp.headers["HX-Trigger"] == "houndarr-connection-test-failure"

    def test_optional_trigger_omitted_when_none(self) -> None:
        resp = hx_retarget_response(
            HTMLResponse(content="x", status_code=422),
            target="#slot",
            reswap="innerHTML",
        )
        assert "HX-Trigger" not in resp.headers

    def test_optional_trigger_omitted_when_explicit_none(self) -> None:
        resp = hx_retarget_response(
            HTMLResponse(content="x", status_code=422),
            target="#slot",
            reswap="innerHTML",
            trigger=None,
        )
        assert "HX-Trigger" not in resp.headers

    def test_alternate_reswap_modes(self) -> None:
        for mode in ("outerHTML", "beforebegin", "afterend", "none"):
            resp = hx_retarget_response(
                Response(status_code=422),
                target="#slot",
                reswap=mode,
            )
            assert resp.headers["HX-Reswap"] == mode

    def test_returns_same_instance(self) -> None:
        original = HTMLResponse(content="x", status_code=422)
        returned = hx_retarget_response(
            original,
            target="#slot",
            reswap="innerHTML",
        )
        assert returned is original

    def test_preserves_422_status_code(self) -> None:
        resp = hx_retarget_response(
            HTMLResponse(content="x", status_code=422),
            target="#slot",
            reswap="innerHTML",
        )
        assert resp.status_code == 422
