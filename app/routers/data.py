"""JSON backup, restore and reset."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import clear_all_data, export_data, import_data
from app.database.migrations import LATEST_VERSION
from app.deps import backfill_transactions

router = APIRouter()


@router.get("/api/export", summary="Download all data as JSON")
async def export_all_data() -> JSONResponse:
    """A JSON backup of every table, offered as a download."""
    tables = await export_data(settings.DB_PATH)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    payload = {
        "schema_version": LATEST_VERSION,
        "app": settings.APP_NAME,
        "app_version": settings.APP_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "tables": tables,
    }
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="sms-webhook-{stamp}.json"'},
    )


@router.post("/api/import", summary="Import data from a JSON export")
async def import_all_data(request: Request, mode: str = "merge") -> dict[str, Any]:
    """
    Restore data from a JSON export. `mode=merge` (default) keeps existing rows and skips
    duplicates; `mode=replace` wipes every table first.
    """
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=422, detail="mode must be 'merge' or 'replace'")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    tables = payload.get("tables", payload)
    if not isinstance(tables, dict):
        raise HTTPException(status_code=400, detail="Expected a 'tables' object")
    counts = await import_data(settings.DB_PATH, tables, mode)
    await backfill_transactions()
    return {"status": "ok", "mode": mode, "counts": counts}


@router.delete("/api/data", summary="Delete all data and reset to a fresh database")
async def reset_all_data() -> dict[str, Any]:
    """Wipe every table (webhooks, transactions, banks, names, gnucash map/rules) and re-seed."""
    deleted = await clear_all_data(settings.DB_PATH)
    return {"status": "cleared", "deleted": deleted}
