"""Settings page routes: instance management via HTMX.

The settings surface is split across three sibling modules so no single
file grows past the 300-line soft cap:

* :mod:`page` owns GET /settings itself.
* :mod:`account` owns /settings/account/* (admin password change).
* :mod:`instances` owns /settings/instances/* (CRUD, test-connection,
  toggle-enabled).

Shared rendering, validation, and client-construction helpers live in
:mod:`_helpers`.  This package composes the three sub-routers into a
single ``router`` that ``houndarr.app`` mounts unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

from houndarr.routes.settings import account, instances, page

router = APIRouter()
router.include_router(page.router)
router.include_router(account.router)
router.include_router(instances.router)

__all__ = ["router"]
