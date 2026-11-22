"""
SQLite storage layer.

Split by concern: `core` (connection + numbered migrations + backup/restore), `webhooks`
(raw messages), `transactions` (parsed bank transactions, cards, banks) and `gnucash`
(account mapping, category rules, posting state). Everything is re-exported here so
`from app.database import ...` keeps working.
"""

from app.database.cards import (
    get_card_statements,
    get_cards,
    save_card_statement,
    set_latest_statement_paid,
)
from app.database.core import (
    EXPORT_TABLES,
    clear_all_data,
    export_data,
    get_db_connection,
    import_data,
    init_db,
)
from app.database.gnucash import (
    claim_transaction_for_posting,
    decorate_suggestions,
    get_card_defaults,
    get_category_rules,
    get_gnucash_account_map,
    record_confirmed_rule,
    record_learned_rules,
    reset_interrupted_posts,
    set_gnucash_account_map,
    set_transaction_choice,
    set_transaction_post_failed,
    set_transaction_posted,
)
from app.database.transactions import (
    TRANSACTION_SELECT,
    delete_bank,
    get_accounts,
    get_bank_map,
    get_banks,
    get_sender_headers,
    get_transaction_by_id,
    get_transactions,
    get_transactions_by_ids,
    save_transaction,
    set_account_name,
    set_bank,
    set_transaction_read,
)
from app.database.webhooks import (
    clear_all_webhooks,
    delete_webhook,
    get_all_webhooks,
    get_stats,
    get_webhook_by_event_id,
    get_webhook_by_id,
    get_webhooks,
    get_webhooks_without_transaction,
    save_webhook,
    set_webhook_read,
    update_webhooks_detected_types,
)

__all__ = [
    "EXPORT_TABLES",
    "TRANSACTION_SELECT",
    "claim_transaction_for_posting",
    "clear_all_data",
    "clear_all_webhooks",
    "decorate_suggestions",
    "delete_bank",
    "delete_webhook",
    "export_data",
    "get_accounts",
    "get_all_webhooks",
    "get_bank_map",
    "get_banks",
    "get_card_defaults",
    "get_card_statements",
    "get_cards",
    "get_category_rules",
    "get_db_connection",
    "get_gnucash_account_map",
    "get_sender_headers",
    "get_stats",
    "get_transaction_by_id",
    "get_transactions",
    "get_transactions_by_ids",
    "get_webhook_by_event_id",
    "get_webhook_by_id",
    "get_webhooks",
    "get_webhooks_without_transaction",
    "import_data",
    "init_db",
    "record_confirmed_rule",
    "record_learned_rules",
    "reset_interrupted_posts",
    "save_card_statement",
    "save_transaction",
    "save_webhook",
    "set_account_name",
    "set_bank",
    "set_gnucash_account_map",
    "set_latest_statement_paid",
    "set_transaction_choice",
    "set_transaction_post_failed",
    "set_transaction_posted",
    "set_transaction_read",
    "set_webhook_read",
    "update_webhooks_detected_types",
]
