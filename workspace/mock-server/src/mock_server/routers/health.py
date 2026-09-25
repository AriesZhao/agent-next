"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health", summary="Health check")
def health() -> dict[str, str]:
    return {"status": "ok"}
