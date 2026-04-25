"""Shared helpers for HTMX-aware route handlers.

HTMX sets ``HX-Request: true`` on every request it issues.  Routes
that can be reached both directly (full page navigation or reload)
and via an HTMX swap call :func:`is_hx_request` to decide which
template to return: a full page with the shell for direct hits, or
a partial for HTMX to swap into ``#app-content``.

On the response side, HTMX inspects a small set of opt-in headers
to decide what to do after the swap.  ``HX-Refresh`` forces a full
reload, ``HX-Redirect`` sends the browser to a new URL,
``HX-Trigger`` and its after-swap variant dispatch a custom DOM
event, and ``HX-Retarget`` + ``HX-Reswap`` redirect the swap into
a different DOM slot so a 4xx/5xx carrying a validation error can
render into a status banner instead of the originating form.  F.3
centralises every one of those names in a one-line helper so a
typo or rename cannot silently break the client contract.

Helpers take an existing ``Response`` (a bare ``Response``,
``HTMLResponse``, or ``TemplateResponse``), set the header, and
return the same instance so call sites can chain the helper over a
freshly constructed response.  The PEP 695 type parameter preserves
the concrete response subtype through the call.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response


def is_hx_request(request: Request) -> bool:
    """Return True when *request* carries the ``HX-Request: true`` header."""
    return request.headers.get("HX-Request") == "true"


def hx_refresh_response[R: Response](response: R) -> R:
    """Set ``HX-Refresh: true`` on *response* so HTMX forces a full reload.

    Used after a session-affecting change (password rotation, secret
    rotation) where every hidden csrf_token input and the body-level
    hx-headers attribute need to be re-stamped from a fresh cookie.
    """
    response.headers["HX-Refresh"] = "true"
    return response


def hx_redirect_response[R: Response](response: R, location: str) -> R:
    """Set ``HX-Redirect: <location>`` so HTMX navigates to *location*.

    HTMX unconditionally follows ``HX-Redirect``, even on responses
    whose status + body would otherwise be swapped into the page,
    which is what makes the factory-reset flow safe: the browser
    moves to /setup before the in-process reset tears the app state
    down.
    """
    response.headers["HX-Redirect"] = location
    return response


def hx_trigger_response[R: Response](response: R, event: str) -> R:
    """Set ``HX-Trigger: <event>`` to dispatch a custom DOM event before swap.

    Listeners bound via ``htmx:`` or DOM ``addEventListener`` fire
    before HTMX swaps the response body into the DOM.  Use this when
    the listener does not need the new content in place (toasts,
    UI-state flips, analytics).
    """
    response.headers["HX-Trigger"] = event
    return response


def hx_trigger_after_swap[R: Response](response: R, event: str) -> R:
    """Set ``HX-Trigger-After-Swap: <event>`` to dispatch after the swap lands.

    Prefer this over :func:`hx_trigger_response` whenever the listener
    needs to look up an element by id: plain ``HX-Trigger`` fires
    before the swap so ``getElementById`` returns ``null``, which
    silently breaks the handler.
    """
    response.headers["HX-Trigger-After-Swap"] = event
    return response


def hx_retarget_response[R: Response](
    response: R,
    *,
    target: str,
    reswap: str,
    trigger: str | None = None,
) -> R:
    """Set ``HX-Retarget`` + ``HX-Reswap``, plus an optional ``HX-Trigger``.

    HTMX 2.x's global ``responseHandling`` config (declared in
    base.html) swaps on 2xx + 422 only; everything else is treated as
    an error with no swap.  A route that wants to render a validation
    error into a different DOM slot (redirect a failed save into the
    connection-status banner rather than reseating the form) sets
    ``HX-Retarget`` to override the default target and ``HX-Reswap``
    to re-enable the swap mode for the error response.  The optional
    *trigger* dispatches a custom event alongside the retarget, used
    when the client listener needs to flash the relevant pill or
    toggle a progress indicator.

    Args:
        response: the response to mutate in place.
        target: CSS selector HTMX should swap into instead of the
            originating element.
        reswap: HTMX ``hx-swap`` mode to use for this response (e.g.
            ``"innerHTML"``).
        trigger: optional custom event name for ``HX-Trigger``.

    Returns:
        The mutated *response*.
    """
    response.headers["HX-Retarget"] = target
    response.headers["HX-Reswap"] = reswap
    if trigger is not None:
        response.headers["HX-Trigger"] = trigger
    return response
