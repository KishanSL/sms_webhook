"""Raw webhook/SMS storage and queries."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database.core import get_db_connection


async def save_webhook(
    db_path: str,
    event_id: str,
    source: str,
    method: str,
    path: str,
    url: str,
    headers: dict[str, str],
    query_params: dict[str, Any] | None,
    content_type: str | None,
    payload_raw: str,
    payload_json: Any | None,
    sender: str | None,
    recipient: str | None,
    message_body: str | None,
    external_message_id: str | None,
    device_code: str | None = None,
    device_id: str | None = None,
    message_type: str | None = None,
    occurred_at: str | None = None,
    channel_id: str | None = None,
    source_name: str | None = None,
    provider: str | None = None,
    client_ip: str | None = None,
    response_status: int = 200,
    received_at: str | None = None,
    detected_type: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """
    Insert incoming webhook/SMS event into SQLite.
    Returns: (record_dict, is_duplicate)
    """
    if received_at is None:
        received_at = datetime.now(UTC).isoformat()

    existing = await get_webhook_by_event_id(db_path, event_id)
    if existing:
        return existing, True

    headers_str = json.dumps(headers, ensure_ascii=False)
    query_params_str = json.dumps(query_params or {}, ensure_ascii=False)
    payload_json_str = json.dumps(payload_json, ensure_ascii=False) if payload_json is not None else None

    sql = """
    INSERT INTO webhooks (
        event_id, received_at, occurred_at, source, source_name, provider,
        device_code, device_id, message_type, channel_id, method, path, url,
        headers, query_params, content_type, sender, recipient,
        message_body, external_message_id, payload_raw,
        payload_json, client_ip, response_status, detected_type
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_id) DO NOTHING
    RETURNING *;
    """

    async with get_db_connection(db_path) as conn:
        async with conn.execute(
            sql,
            (
                event_id,
                received_at,
                occurred_at,
                source,
                source_name,
                provider,
                device_code,
                device_id,
                message_type,
                channel_id,
                method,
                path,
                url,
                headers_str,
                query_params_str,
                content_type,
                sender,
                recipient,
                message_body,
                external_message_id,
                payload_raw,
                payload_json_str,
                client_ip,
                response_status,
                detected_type,
            ),
        ) as cursor:
            row = await cursor.fetchone()
            await conn.commit()
            if row:
                return dict(row), False

    # A concurrent request inserted the same event_id first: return that row as a duplicate.
    existing = await get_webhook_by_event_id(db_path, event_id)
    if existing:
        return existing, True
    raise RuntimeError("Database insertion failed")


async def get_webhooks(
    db_path: str,
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    sender: str | None = None,
    search: str | None = None,
    type_filter: str | None = None,
    is_read: bool | None = None,
) -> list[dict[str, Any]]:
    """Retrieve webhook logs with optional filtering, type filtering, read state, and search."""
    query = "SELECT * FROM webhooks WHERE 1=1"
    params: list[Any] = []

    if is_read is not None:
        query += " AND is_read = ?"
        params.append(int(is_read))

    if source:
        query += " AND (source = ? OR provider = ?)"
        params.extend([source, source])

    if sender:
        query += " AND sender LIKE ?"
        params.append(f"%{sender}%")

    if type_filter:
        query += " AND detected_type = ?"
        params.append(type_filter)

    if search:
        query += """ AND (
            message_body LIKE ? OR
            payload_raw LIKE ? OR
            sender LIKE ? OR
            recipient LIKE ? OR
            device_code LIKE ? OR
            message_type LIKE ? OR
            path LIKE ? OR
            event_id LIKE ?
        )"""
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern])

    query += " ORDER BY received_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    async with get_db_connection(db_path) as conn:
        async with conn.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_webhook_by_id(db_path: str, webhook_id: int) -> dict[str, Any] | None:
    """Fetch single webhook record by internal ID."""
    async with get_db_connection(db_path) as conn:
        async with conn.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_webhook_by_event_id(db_path: str, event_id: str) -> dict[str, Any] | None:
    """Fetch single webhook record by unique event_id."""
    async with get_db_connection(db_path) as conn:
        async with conn.execute("SELECT * FROM webhooks WHERE event_id = ?", (event_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def set_webhook_read(db_path: str, webhook_id: int, is_read: bool) -> bool:
    """Mark a webhook record as read or unread. Also marks matching transaction as read / unread."""
    read_at = datetime.now(UTC).isoformat() if is_read else None
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE webhooks SET is_read = ?, read_at = ? WHERE id = ?",
            (int(is_read), read_at, webhook_id),
        )
        if cursor.rowcount > 0:
            await conn.execute(
                """
                UPDATE transactions
                SET is_read = ?, read_at = ?
                WHERE webhook_id = ?
                   OR event_id = (SELECT event_id FROM webhooks WHERE id = ?)
                """,
                (int(is_read), read_at, webhook_id, webhook_id),
            )
            await conn.commit()
            return True
        return False


async def delete_webhook(db_path: str, webhook_id: int) -> bool:
    """Delete a webhook record by ID and unlink any referencing transactions or statements."""
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        if cursor.rowcount > 0:
            await conn.execute("UPDATE transactions SET webhook_id = NULL WHERE webhook_id = ?", (webhook_id,))
            await conn.execute("UPDATE card_statements SET webhook_id = NULL WHERE webhook_id = ?", (webhook_id,))
            await conn.commit()
            return True
        return False


async def clear_all_webhooks(db_path: str) -> int:
    """Clear all records from webhooks table and unlink any referencing transactions or statements."""
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute("DELETE FROM webhooks")
        await conn.execute("UPDATE transactions SET webhook_id = NULL")
        await conn.execute("UPDATE card_statements SET webhook_id = NULL")
        await conn.commit()
        return cursor.rowcount


async def get_webhooks_without_transaction(db_path: str) -> list[dict[str, Any]]:
    """Fetch webhooks with a sender and body that have no transaction row yet (backfill candidates)."""
    sql = """
    SELECT * FROM webhooks
    WHERE sender IS NOT NULL
      AND message_body IS NOT NULL
      AND event_id NOT IN (SELECT event_id FROM transactions)
    ORDER BY id
    """
    async with get_db_connection(db_path) as conn:
        async with conn.execute(sql) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_stats(db_path: str) -> dict[str, Any]:
    """Compute summary statistics for dashboard and health monitor."""
    if not Path(db_path).exists():
        return {
            "total_count": 0,
            "db_size_bytes": 0,
            "sources": {},
            "providers": {},
            "devices": {},
            "latest_received_at": None,
        }

    db_size = os.path.getsize(db_path)

    async with get_db_connection(db_path) as conn:
        async with conn.execute(
            "SELECT COUNT(*) AS total, MAX(received_at) AS latest FROM webhooks"
        ) as cursor:
            row = await cursor.fetchone()
            total_count = row["total"] if row else 0
            latest_received_at = row["latest"] if row else None

        sources = {}
        async with conn.execute(
            "SELECT source, COUNT(*) AS count FROM webhooks GROUP BY source ORDER BY count DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                sources[r["source"]] = r["count"]

        providers = {}
        async with conn.execute(
            "SELECT COALESCE(provider, 'unknown') as prov, COUNT(*) AS count FROM webhooks GROUP BY prov ORDER BY count DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                providers[r["prov"]] = r["count"]

        devices = {}
        async with conn.execute(
            "SELECT device_code, COUNT(*) AS count FROM webhooks WHERE device_code IS NOT NULL GROUP BY device_code ORDER BY count DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                devices[r["device_code"]] = r["count"]

        return {
            "total_count": total_count,
            "db_size_bytes": db_size,
            "sources": sources,
            "providers": providers,
            "devices": devices,
            "latest_received_at": latest_received_at,
        }


async def update_webhooks_detected_types(db_path: str, updates: list[tuple[str, int]]) -> None:
    """Batch-update detected_type for a list of (detected_type, webhook_id) tuples."""
    if not updates:
        return
    async with get_db_connection(db_path) as conn:
        await conn.executemany("UPDATE webhooks SET detected_type = ? WHERE id = ?", updates)
        await conn.commit()


async def get_all_webhooks(db_path: str) -> list[dict[str, Any]]:
    """Fetch all webhooks ordered by id ASC."""
    async with get_db_connection(db_path) as conn:
        async with conn.execute("SELECT * FROM webhooks ORDER BY id ASC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
