"""Webhook ingestion and the raw-message management API."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.classifier import classify_message_text, enrich_webhooks_with_types
from app.config import settings
from app.database import (
    clear_all_webhooks,
    delete_webhook,
    get_bank_map,
    get_webhook_by_event_id,
    get_webhook_by_id,
    get_webhooks,
    save_webhook,
    set_webhook_read,
)
from app.deps import (
    backfill_transactions,
    extract_client_ip,
    logger,
    record_card_statement,
    record_transaction,
    retag_all_webhooks,
    verify_authentication,
)
from app.sms_parser import parse_sms_payload

router = APIRouter()


@router.api_route(
    "/webhook",
    methods=["POST", "PUT", "PATCH", "GET", "HEAD"],
    summary="Generic default webhook endpoint",
)
@router.api_route(
    "/webhook/sms",
    methods=["POST", "PUT", "PATCH", "GET", "HEAD"],
    summary="SMS webhook endpoint",
)
@router.api_route(
    "/webhook/message487",
    methods=["POST", "PUT", "PATCH", "GET", "HEAD"],
    summary="Message487 Android SMS forwarder endpoint",
)
@router.api_route(
    "/sms",
    methods=["POST", "PUT", "PATCH", "GET", "HEAD"],
    summary="Direct /sms webhook endpoint",
)
@router.api_route(
    "/webhook/{source:path}",
    methods=["POST", "PUT", "PATCH", "GET", "HEAD"],
    summary="Dynamic source webhook endpoint",
)
async def receive_webhook(request: Request, source: str = "webhook") -> Response:
    """
    Primary webhook ingestion receiver.
    Captures headers, query parameters, raw body, parses JSON/form-data,
    extracts Message487 and SMS metadata, and persists to SQLite.
    Returns format accepted by Message487 confirmation mode & standard webhooks.
    """
    if request.method == "HEAD":
        return Response(status_code=status.HTTP_200_OK)

    verify_authentication(request)

    client_ip = extract_client_ip(request)
    headers = dict(request.headers)
    query_params = dict(request.query_params)
    content_type = request.headers.get("content-type", "")

    # Read payload bytes
    body_bytes = await request.body()
    if len(body_bytes) > settings.MAX_BODY_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload exceeds maximum allowed size",
        )

    # Decode raw body
    try:
        payload_raw = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        payload_raw = body_bytes.decode("latin1", errors="replace")

    # Attempt to parse JSON or Form data
    payload_json: Any | None = None
    form_data: dict[str, Any] | None = None

    if "application/json" in content_type.lower() or (
        payload_raw.strip().startswith("{") and payload_raw.strip().endswith("}")
    ):
        try:
            payload_json = json.loads(payload_raw)
        except Exception:
            payload_json = None

    if "application/x-www-form-urlencoded" in content_type.lower() or "multipart/form-data" in content_type.lower():
        try:
            form = await request.form()
            form_data = {k: str(v) for k, v in form.items()}
        except Exception:
            form_data = None

    # Parse SMS fields (supports Message487 and generic payloads)
    parsed = parse_sms_payload(payload_json=payload_json, form_data=form_data)

    # Extract event_id:
    # 1. From payload event_id / external_message_id (Message487 sends 'event_id')
    # 2. X-Event-Id / X-Request-Id header
    # 3. Generated UUIDv4
    event_id = (
        parsed.get("external_message_id")
        or (payload_json.get("event_id") if isinstance(payload_json, dict) else None)
        or headers.get("x-event-id")
        or headers.get("x-request-id")
        or str(uuid.uuid4())
    )

    # Source determination
    effective_source = source
    if parsed.get("provider") and parsed["provider"] != "generic":
        effective_source = parsed["provider"]

    banks = await get_bank_map(settings.DB_PATH)
    detected_type = classify_message_text(
        text=parsed.get("message_body"),
        sender=parsed.get("sender"),
        banks=banks,
    )

    # Save to SQLite with deduplication
    record, is_duplicate = await save_webhook(
        db_path=settings.DB_PATH,
        event_id=event_id,
        source=effective_source,
        source_name=parsed.get("source_name"),
        provider=parsed.get("provider"),
        device_code=parsed.get("device_code"),
        device_id=parsed.get("device_id"),
        message_type=parsed.get("message_type"),
        channel_id=parsed.get("channel_id"),
        occurred_at=parsed.get("occurred_at"),
        method=request.method,
        path=request.url.path,
        url=str(request.url),
        headers=headers,
        query_params=query_params,
        content_type=content_type,
        sender=parsed.get("sender"),
        recipient=parsed.get("recipient"),
        message_body=parsed.get("message_body"),
        external_message_id=parsed.get("external_message_id"),
        payload_raw=payload_raw,
        payload_json=payload_json or form_data,
        client_ip=client_ip,
        response_status=200,
        detected_type=detected_type,
    )

    # Extract bank money-movement SMS into the transactions table.
    # Failures are logged, not raised, so the webhook is still acknowledged.
    if not is_duplicate:
        try:
            await record_transaction(record, banks)
        except Exception:
            logger.exception("Transaction parsing failed for event %s", event_id)
        try:
            await record_card_statement(record, banks)
        except Exception:
            logger.exception("Statement parsing failed for event %s", event_id)

    # Return structure expected by Message487
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "accepted",
            "event_id": event_id,
            "received_at": record["received_at"],
            "duplicate": is_duplicate,
        },
    )


class ReadState(BaseModel):
    read: bool


@router.get("/api/webhooks", summary="List stored webhooks")
async def list_webhooks(
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    sender: str | None = None,
    search: str | None = None,
    type: str | None = None,
    read: bool | None = None,
) -> list[dict[str, Any]]:
    """Retrieve stored webhooks with filtering, search, and pagination."""
    rows = await get_webhooks(
        db_path=settings.DB_PATH,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
        source=source,
        sender=sender,
        search=search,
        type_filter=type,
        is_read=read,
    )
    return await enrich_webhooks_with_types(rows)


@router.put("/api/webhooks/{webhook_id}/read", summary="Mark a webhook read / unread")
async def mark_webhook_read(webhook_id: int, body: ReadState) -> dict[str, Any]:
    if not await set_webhook_read(settings.DB_PATH, webhook_id, body.read):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"id": webhook_id, "read": body.read}


@router.get("/api/webhooks/{webhook_id}", summary="Get single webhook by ID")
async def get_single_webhook(webhook_id: int) -> dict[str, Any]:
    record = await get_webhook_by_id(settings.DB_PATH, webhook_id)
    if not record:
        raise HTTPException(status_code=404, detail="Webhook not found")
    enriched = await enrich_webhooks_with_types([record])
    return enriched[0]


@router.get("/api/webhooks/event/{event_id}", summary="Get single webhook by event_id")
async def get_by_event_id(event_id: str) -> dict[str, Any]:
    record = await get_webhook_by_event_id(settings.DB_PATH, event_id)
    if not record:
        raise HTTPException(status_code=404, detail="Webhook not found")
    enriched = await enrich_webhooks_with_types([record])
    return enriched[0]


@router.delete("/api/webhooks/{webhook_id}", summary="Delete a webhook")
async def delete_single_webhook(webhook_id: int) -> dict[str, Any]:
    success = await delete_webhook(settings.DB_PATH, webhook_id)
    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted", "id": webhook_id}


@router.delete("/api/webhooks", summary="Clear all stored webhooks")
async def clear_all() -> dict[str, Any]:
    deleted_count = await clear_all_webhooks(settings.DB_PATH)
    return {"status": "cleared", "deleted_count": deleted_count}


@router.post(
    "/api/webhooks/backfill",
    summary="Re-classify and retag all messages, and backfill transactions & statements",
)
async def run_webhooks_backfill() -> dict[str, Any]:
    """
    Re-evaluates every stored webhook message with updated classification rules
    and bank mappings, updates `detected_type` in SQLite, and runs transaction
    and card statement backfill.
    """
    txns_added = await backfill_transactions(retag=False)
    retag_res = await retag_all_webhooks(settings.DB_PATH)
    return {
        **retag_res,
        "transactions_added": txns_added,
    }
