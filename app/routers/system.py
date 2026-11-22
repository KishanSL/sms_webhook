"""Certificates, favicon, health and service stats."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from app.config import settings
from app.database import get_stats

router = APIRouter()


@router.get("/ca.crt", summary="Download Root CA Certificate for Android installation")
@router.head("/ca.crt")
async def download_ca_crt() -> FileResponse:
    """Download the Root CA in PEM format (.crt) for Android certificate installer."""
    ca_path = Path(settings.CERT_DIR) / "ca.crt"
    if not ca_path.exists():
        raise HTTPException(status_code=404, detail="CA Certificate not found")
    return FileResponse(
        path=str(ca_path),
        filename="homelab-ca.crt",
        media_type="application/x-x509-ca-cert",
    )


@router.get("/ca.der", summary="Download Root CA Certificate in binary DER format")
@router.head("/ca.der")
async def download_ca_der() -> FileResponse:
    """Download the Root CA in DER binary format for older/specific Android installers."""
    ca_path = Path(settings.CERT_DIR) / "ca.der"
    if not ca_path.exists():
        raise HTTPException(status_code=404, detail="CA DER Certificate not found")
    return FileResponse(
        path=str(ca_path),
        filename="homelab-ca.der",
        media_type="application/x-x509-ca-cert",
    )


# A small emerald rupee mark, served for both /favicon.ico and /favicon.svg so browsers
# (and anything that probes /favicon.ico directly) get a 200 instead of a 404.
FAVICON_SVG = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
    b"stroke='#16a34a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
    b"<circle cx='12' cy='12' r='10'/>"
    b"<path d='M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8'/>"
    b"<path d='M12 6v2'/><path d='M12 16v2'/></svg>"
)


@router.get("/favicon.ico", include_in_schema=False)
@router.get("/favicon.svg", include_in_schema=False)
async def favicon() -> Response:
    return Response(
        content=FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/stats", summary="Get service metrics and database size")
async def get_service_stats() -> dict[str, Any]:
    return await get_stats(settings.DB_PATH)


@router.get("/health", summary="Health check endpoint")
@router.head("/health")
async def health_check() -> dict[str, Any]:
    stats = await get_stats(settings.DB_PATH)
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "https_enabled": settings.HTTPS_ENABLED,
        "database": {
            "path": settings.DB_PATH,
            "total_records": stats["total_count"],
            "size_bytes": stats["db_size_bytes"],
        },
    }
