"""GnuCash-web mapping, category rules, suggestions and posting state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.database.core import get_db_connection
from app.gnucash import card_scope, choose_suggestion, display_merchant, normalise_merchant


async def get_gnucash_account_map(db_path: str) -> dict[str, str]:
    """Map 'SENDER:last4' to the gnucash account each card posts from."""
    async with get_db_connection(db_path) as conn:
        async with conn.execute("SELECT sender_header, account_last4, gnucash_account_id FROM gnucash_account_map") as cursor:
            return {f"{r['sender_header']}:{r['account_last4']}": r["gnucash_account_id"] for r in await cursor.fetchall()}


async def set_gnucash_account_map(db_path: str, sender_header: str, account_last4: str, gnucash_account_id: str | None) -> None:
    """Set (or clear, when empty) the gnucash From account for a card, and backfill its rows."""
    async with get_db_connection(db_path) as conn:
        if gnucash_account_id:
            await conn.execute(
                """
                INSERT INTO gnucash_account_map (sender_header, account_last4, gnucash_account_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sender_header, account_last4)
                DO UPDATE SET gnucash_account_id = excluded.gnucash_account_id, updated_at = excluded.updated_at
                """,
                (sender_header, account_last4, gnucash_account_id, datetime.now(UTC).isoformat()),
            )
            await conn.execute(
                """
                UPDATE transactions
                SET from_account_id = ?
                WHERE sender_header = ? AND account_last4 = ?
                  AND (post_status IS NULL OR post_status != 'posted')
                """,
                (gnucash_account_id, sender_header, account_last4),
            )
        else:
            await conn.execute(
                "DELETE FROM gnucash_account_map WHERE sender_header = ? AND account_last4 = ?",
                (sender_header, account_last4),
            )
            await conn.execute(
                """
                UPDATE transactions
                SET from_account_id = NULL
                WHERE sender_header = ? AND account_last4 = ?
                  AND (post_status IS NULL OR post_status != 'posted')
                """,
                (sender_header, account_last4),
            )
        await conn.commit()


async def get_category_rules(db_path: str) -> list[dict[str, Any]]:
    async with get_db_connection(db_path) as conn:
        async with conn.execute(
            "SELECT merchant_key, scope, source, gnucash_account_id, hits, last_used_at FROM category_rules"
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def record_confirmed_rule(db_path: str, merchant_key: str, scope: str, gnucash_account_id: str) -> None:
    """
    Add one hit to a confirmed merchant → account choice (from a successful Post).
    Record both the card-specific rule and a `*` (any card) rule, so suggestion step 1's
    "then any card" fallback can match.
    """
    if not merchant_key:
        return
    now = datetime.now(UTC).isoformat()
    rows = [(merchant_key, scope, gnucash_account_id, now)]
    if scope != "*":
        rows.append((merchant_key, "*", gnucash_account_id, now))
    async with get_db_connection(db_path) as conn:
        await conn.executemany(
            """
            INSERT INTO category_rules (merchant_key, scope, source, gnucash_account_id, hits, last_used_at)
            VALUES (?, ?, 'confirmed', ?, 1, ?)
            ON CONFLICT(merchant_key, scope, source, gnucash_account_id)
            DO UPDATE SET hits = hits + 1, last_used_at = excluded.last_used_at
            """,
            rows,
        )
        await conn.commit()


async def record_learned_rules(db_path: str, counts: dict[tuple[str, str], int]) -> int:
    """Replace the 'learned from gnucash history' hints with the given (merchant, account) counts."""
    now = datetime.now(UTC).isoformat()
    async with get_db_connection(db_path) as conn:
        await conn.execute("DELETE FROM category_rules WHERE source = 'learned'")
        await conn.executemany(
            """
            INSERT INTO category_rules (merchant_key, scope, source, gnucash_account_id, hits, last_used_at)
            VALUES (?, '*', 'learned', ?, ?, ?)
            """,
            [(merchant, account, count, now) for (merchant, account), count in counts.items()],
        )
        await conn.commit()
    return len(counts)


async def get_card_defaults(db_path: str) -> dict[str, tuple[str, int]]:
    """Most-frequent posted 'To' account per card scope, from already-posted transactions."""
    sql = """
    SELECT sender_header || ':' || account_last4 AS scope, to_account_id, COUNT(*) AS n
    FROM transactions
    WHERE to_account_id IS NOT NULL AND account_last4 IS NOT NULL AND post_status = 'posted'
    GROUP BY scope, to_account_id
    """
    async with get_db_connection(db_path) as conn:
        async with conn.execute(sql) as cursor:
            rows = await cursor.fetchall()
    defaults: dict[str, tuple[str, int]] = {}
    for r in rows:
        scope, account_id, n = r["scope"], r["to_account_id"], r["n"]
        current = defaults.get(scope)
        if current is None or n > current[1]:
            defaults[scope] = (account_id, n)
    return defaults


async def decorate_suggestions(db_path: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Attach `suggested_to_account_id` and `suggestion_reason` to each transaction row, and
    persist `from_account_id`, `suggestion_reason` and the `suggested` status when they change.
    A chosen/confirmed To (`to_account_id`) wins; otherwise the engine suggests; otherwise None,
    which the UI shows as "Uncategorised".
    """
    if not rows:
        return rows
    rules = await get_category_rules(db_path)
    defaults = await get_card_defaults(db_path)
    updates: list[tuple[Any, ...]] = []
    for row in rows:
        mapped = row.get("mapped_from_account_id")
        old_from = row.get("from_account_id")
        old_reason = row.get("suggestion_reason") or ""
        old_status = row.get("post_status") or "new"

        if old_status == "posted":
            from_id = old_from
        else:
            from_id = mapped or old_from
        row["from_account_id"] = from_id

        if row.get("to_account_id"):
            account_id = row["to_account_id"]
            reason = old_reason or ("already posted" if old_status == "posted" else "chosen")
            new_status = old_status
        else:
            merchant_key = row.get("merchant_key") or normalise_merchant(row.get("counterparty"))
            scope = card_scope(row.get("sender_header"), row.get("account_last4"))
            account_id, reason = choose_suggestion(
                merchant_key,
                display_merchant(row.get("counterparty")),
                scope,
                rules,
                defaults,
            )
            new_status = old_status
            if old_status == "new":
                new_status = "suggested" if account_id else "new"

        row["suggested_to_account_id"] = account_id
        row["suggestion_reason"] = reason
        row["in_ledger"] = bool(row.get("gnucash_tx_id"))
        if old_status != "posting":
            row["post_status"] = new_status
            if from_id != old_from or reason != old_reason or new_status != old_status:
                updates.append((from_id, reason, new_status, row["id"], old_status))

    if updates:
        async with get_db_connection(db_path) as conn:
            # Guard on the status we read: a concurrent Post may have claimed or completed the row
            # since the SELECT. Never overwrite `posting`/`posted` from a stale read.
            await conn.executemany(
                """
                UPDATE transactions
                SET from_account_id = ?, suggestion_reason = ?, post_status = ?
                WHERE id = ? AND post_status = ?
                """,
                updates,
            )
            await conn.commit()
    return rows


async def set_transaction_choice(db_path: str, transaction_id: int, to_account_id: str | None) -> bool:
    """Persist the user's To choice without posting (status becomes `suggested`)."""
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            """
            UPDATE transactions
            SET to_account_id = ?,
                post_error = CASE WHEN post_status IN ('new', 'suggested', 'failed') THEN NULL ELSE post_error END,
                post_status = CASE WHEN post_status IN ('new', 'suggested', 'failed') THEN 'suggested' ELSE post_status END
            WHERE id = ?
            """,
            (to_account_id or None, transaction_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def claim_transaction_for_posting(db_path: str, transaction_id: int) -> bool:
    """
    Claim a row for posting. Refuses rows that are already being posted or are final
    (posted / already in gnucash-web), so a posted transaction can never be claimed again.
    """
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            """
            UPDATE transactions SET post_status = 'posting', post_error = NULL
            WHERE id = ?
              AND post_status NOT IN ('posting', 'posted')
              AND (gnucash_tx_id IS NULL OR gnucash_tx_id = '')
            """,
            (transaction_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def set_transaction_posted(
    db_path: str,
    transaction_id: int,
    to_account_id: str,
    gnucash_tx_id: str,
    from_account_id: str | None = None,
) -> None:
    """Store the gnucash id, mark posted and read. Posted rows are final."""
    now = datetime.now(UTC).isoformat()
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            """
            UPDATE transactions
            SET post_status = 'posted', to_account_id = ?, gnucash_tx_id = ?,
                from_account_id = COALESCE(?, from_account_id), posted_at = ?,
                post_error = NULL, is_read = 1, read_at = ?
            WHERE id = ?
            """,
            (to_account_id, gnucash_tx_id, from_account_id, now, now, transaction_id),
        )
        await conn.execute(
            """
            UPDATE webhooks
            SET is_read = 1, read_at = ?
            WHERE id = (SELECT webhook_id FROM transactions WHERE id = ?)
               OR (event_id IS NOT NULL AND event_id = (SELECT event_id FROM transactions WHERE id = ?))
            """,
            (now, transaction_id, transaction_id),
        )
        await conn.commit()


async def set_transaction_post_failed(db_path: str, transaction_id: int, error: str) -> None:
    """Record a failed post."""
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            "UPDATE transactions SET post_status = 'failed', post_error = ? WHERE id = ?",
            (error, transaction_id),
        )
        await conn.commit()


async def reset_interrupted_posts(db_path: str) -> int:
    """On startup, clear claims left behind by a restart so those rows can be posted again."""
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            """
            UPDATE transactions
            SET post_status = 'failed', post_error = 'previous post was interrupted; press Post again'
            WHERE post_status = 'posting'
            """
        )
        await conn.commit()
        return cursor.rowcount
