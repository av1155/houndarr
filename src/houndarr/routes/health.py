"""Health check endpoint — unauthenticated, used by Docker HEALTHCHECK."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Return 200 OK with a simple status payload."""
    return JSONResponse({"status": "ok"})
