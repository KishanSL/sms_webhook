# Homelab Private HTTPS SMS & Webhook Service

A private, high-performance HTTPS webhook receiver and SQLite storage service designed specifically for homelab environments. Tailored for **Message487** (`life.andre.message487` on Android) and generic SMS/HTTP webhooks.

---

## 📋 Table of Contents
1. [Overview & Features](#overview--features)
2. [Background: The Android TLS Trust Challenge & Homelab Solution](#background-the-android-tls-trust-challenge--homelab-solution)
3. [System Architecture & Codebase Map](#system-architecture--codebase-map)
4. [Component Deep Dive (Developer Guide)](#component-deep-dive-developer-guide)
5. [Database Schema & SQLite Design](#database-schema--sqlite-design)
6. [API Contract & Message487 Protocol](#api-contract--message487-protocol)
7. [Operational Setup & Systemd Deployment](#operational-setup--systemd-deployment)
8. [Certificate Lifecycle & Renewal](#certificate-lifecycle--renewal)
9. [Testing & Verification](#testing--verification)
10. [Docker & Podman Compose Production Deployment](#docker--podman-compose-production-deployment)
11. [Git Repository & Workflow](#git-repository--workflow)

---

## 1. Overview & Features

- **Private & Zero Public Exposure:** Runs strictly on your home local area network (`192.168.1.10`). No ports are forwarded on your router; no public traffic can reach your machine.
- **Android System TLS Trust:** Uses a valid Let's Encrypt certificate generated via Cloudflare DNS-01 challenge for `sms.example.com`, satisfying Android's strict system-store requirement.
- **Native Message487 Integration:** Seamlessly parses incoming SMS messages, connection test events, and Android notifications forwarded by the Message487 app (`moto-edge-70-fusion`).
- **SQLite with WAL Mode:** Persistent storage using `aiosqlite` with Write-Ahead Logging (`WAL`), `synchronous=NORMAL`, and busy timeout handling for high-concurrency and non-blocking reads/writes.
- **Idempotency & Deduplication:** Tracks incoming `event_id` to prevent duplicate writes during network retries while returning HTTP 200 `status: accepted` so Android clients clear their retry queue.
- **Live Interactive Dashboard:** Web-based UI at `https://sms.example.com:8443/dashboard` featuring live metrics, search filtering, payload modal inspection, and auto-refresh.
- **Dual Listener Support:** Concurrently runs HTTPS (`8443`) and optional plain HTTP (`8080`) in a single async event loop.
- **Bank Transaction Extraction:** Debit/credit SMS from linked bank sender codes (e.g. `AD-HDFCBK-S`) are parsed into a separate `transactions` table (amount, card/account, counterparty, reference, balance/limit). Tolls, statements and OTPs are skipped; raw messages are always kept in `webhooks`.
- **Transactions & Settings Pages:** `/transactions` lists parsed transactions with filters, read/unread checkboxes and links back to the original message; `/settings` links sender codes to banks, names cards/accounts, maps gnucash accounts and offers **JSON export / import / clear-all**.
- **JSON Backup & Restore:** `GET /api/export` downloads every table (banks, names, gnucash map, rules, statements, webhooks, transactions); `POST /api/import` restores it (`merge` keeps existing rows, `replace` wipes first); `DELETE /api/data` clears everything and re-seeds the default banks.
- **Cards Page:** `/cards` shows each credit card's latest statement (total due, min due, due date with days left) parsed from statement SMS. A statement can be **marked paid** and stays listed (dimmed), just like a read transaction.
- **GnuCash-web Posting:** Each transaction's card maps to a gnucash-web "From" account; the "To" account is suggested (from your past choices, gnucash history, or the card's usual destination) and confirmed by you before anything is posted. The ledger stays balanced: the card split is negative and cleared, the other side positive. Unrecognised rows show **Uncategorised** and Post stays disabled until you choose an account — sms_webhook never invents accounts in gnucash-web. **A posted transaction is final:** sms_webhook never edits or re-posts it, so any later change is made in gnucash-web itself.

---

## 2. Background: The Android TLS Trust Challenge & Homelab Solution

### The Problem
1. **Message487 enforces HTTPS:** Release builds of the open-source Android app Message487 require `https://` target URLs.
2. **Android 7+ / 14 Certificate Policy:**
   - When a self-signed Root CA is manually installed on Android (*Settings → Security → Install certificate*), it is placed into the **User** certificate store.
   - Starting with Android 7.0 (API 24) through **Android 14**, third-party apps **only trust the System certificate store** out of the box unless the developer explicitly compiled the app with `<certificates src="user" />` in `network_security_config.xml`.
   - As a result, Message487 immediately fails with `Could not complete the connection` during TLS handshake when pointed at a self-signed certificate, even if the user installed the CA.

### The Solution: Private LAN DNS + Let's Encrypt DNS-01 Challenge
To achieve trusted HTTPS **without exposing any service or port to the public internet**:
```
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  [Android Phone]                                                                  |
|   (Message487)                                                                    |
|        |                                                                          |
|        | 1. Resolves sms.example.com -> 192.168.1.10 (Cloudflare DNS A record)    |
|        | 2. Connects directly over local Wi-Fi to https://192.168.1.10:8443        |
|        | 3. Validates TLS Cert against Android System CA (ISRG Root X1 / X2)      |
|        v                                                                          |
|  [Local Webhook Service @ 192.168.1.10:8443]                                       |
|        |                                                                          |
|        +--> Persists into ~/sms_webhook/data/webhooks.db (SQLite)                 |
|                                                                                   |
|  -------------------------------------------------------------------------------  |
|  Zero public ports open | Zero internet exposure | Traffic never leaves home Wi-Fi |
+-----------------------------------------------------------------------------------+
```

1. **Private A Record:** A Cloudflare DNS record `sms.example.com` points to `192.168.1.10` with **DNS Only (No Proxy)**. Cloudflare marks this as `DNS Only - reserved IP` because `192.168.1.0/24` is an RFC 1918 private range that cannot be routed over the public internet.
2. **DNS-01 Challenge:** Let's Encrypt verifies domain ownership by adding a temporary TXT record to Cloudflare via an API token. **No incoming router ports (80 or 443) are opened.**
3. **Result:** Android's native system trust store recognizes the certificate natively, while all webhook traffic remains 100% inside the local network.

---

## 3. System Architecture & Codebase Map

```
sms_webhook/
├── app/
│   ├── __init__.py           # Package marker
│   ├── config.py             # Settings loader via pydantic-settings & .env
│   ├── database/             # SQLite layer (see below)
│   │   ├── core.py           # Connection, numbered migrations runner, JSON backup/restore
│   │   ├── migrations.py     # Numbered schema migrations (tracked in schema_migrations)
│   │   ├── webhooks.py       # Raw webhook/SMS storage and queries
│   │   ├── transactions.py   # Parsed transactions, cards/accounts, banks, sender headers
│   │   ├── gnucash.py        # Account mapping, category rules, suggestions, posting state
│   │   └── cards.py          # Credit-card statements and the cards summary
│   ├── routers/              # FastAPI routers, one per area
│   │   ├── webhooks.py       # Ingestion + /api/webhooks
│   │   ├── transactions.py   # /api/transactions + gnucash post/to/post-reviewed
│   │   ├── cards.py          # /api/cards (statements, mark paid)
│   │   ├── gnucash.py        # /api/gnucash/*, card mapping
│   │   ├── accounts.py       # /api/accounts, /api/banks, /api/senders
│   │   ├── data.py           # /api/export, /api/import, /api/data
│   │   ├── system.py         # /health, /api/stats, certs, favicon
│   │   └── pages.py          # /dashboard, /transactions, /settings
│   ├── deps.py               # Shared helpers (templates, auth, transaction parsing)
│   ├── sms_parser.py         # Parser for Message487 and generic SMS payloads
│   ├── txn_parser.py         # Bank debit/credit SMS parser (per-bank regex formats)
│   ├── statement_parser.py   # Credit-card statement SMS parser (amounts due, dates)
│   ├── gnucash.py            # Merchant normalising, account paths, post payload, suggestions
│   ├── gnucash_client.py     # Async gnucash-web API client (accounts, transactions)
│   ├── gnucash_post.py       # Posting engine: account cache, submit, duplicates, learn
│   ├── cert_manager.py       # Local self-signed CA generator (fallback)
│   └── main.py               # Assembles the FastAPI app from the routers + lifespan
├── certs/
│   ├── cert.pem              # Active SSL Certificate (Let's Encrypt fullchain)
│   ├── key.pem               # Active SSL Private Key (Let's Encrypt privkey)
│   └── letsencrypt/          # Certbot configuration, work, and logs directories
├── data/
│   ├── .gitkeep              # Directory keeper
│   └── webhooks.db           # SQLite database with WAL mode enabled
├── templates/
│   ├── base.html             # Shared shell (header, nav, body) extended by every page
│   ├── dashboard.html        # Responsive Tailwind CSS dashboard (raw messages)
│   ├── transactions.html     # Parsed bank transactions table
│   ├── cards.html            # Credit cards: pending statement payments, mark paid
│   ├── settings.html         # Bank codes, card names, gnucash mapping, data export/import
│   └── _gnucash_common.html  # Shared gnucash account loader + searchable picker helpers
├── static/
│   └── js/                   # Page JavaScript served from /static
│       ├── common.js         # Shared helpers (escapeHtml, api, money formatters, toasts)
│       ├── dashboard.js
│       ├── transactions.js
│       ├── settings.js
│       └── cards.js
├── scripts/
│   └── send_test_sms.py      # Test sender simulating a Message487 webhook
├── tests/
│   ├── __init__.py           # Tests package
│   ├── test_database.py      # DB unit tests (insert, deduplication, search, stats)
│   ├── test_sms_parsing.py   # Payload parser unit tests
│   ├── test_txn_parser.py    # Bank transaction parser tests (anonymised samples)
│   ├── test_gnucash.py       # gnucash bridge tests (normaliser, payload, suggestions, client)
│   ├── test_gnucash_post.py  # Posting-engine and endpoint tests (idempotency, duplicates, bulk)
│   ├── test_migrations.py    # Numbered schema migrations (fresh + legacy upgrade)
│   ├── test_export_import.py # JSON backup/restore, reset, Settings route
│   ├── test_webhook_api.py   # FastAPI HTTP endpoint tests
│   └── test_message487_payload.py # Integration test for exact user Android payload
├── githooks/
│   └── pre-push              # Runs ruff + mypy + pytest before every push
├── .env.example              # Environment variables template
├── .gitignore                # Excludes venv, keys, DBs, cloudflare.ini, and local binaries
├── pyproject.toml            # ruff + mypy configuration
├── pytest.ini                # Pytest configuration (pythonpath = .)
├── requirements.txt          # Production dependencies
├── requirements-dev.txt      # Adds ruff + mypy for development
├── run.py                    # Multi-protocol runner (Concurrent HTTPS & HTTP)
├── sms_webhook.service       # Systemd service unit file
└── README.md                 # This documentation
```

---

## 4. Component Deep Dive (Developer Guide)

### `app/config.py`
Manages application configuration using `pydantic-settings`:
- `HOST`: Default `0.0.0.0`
- `PORT`: Default `8443` (HTTPS)
- `HTTPS_ENABLED`: Default `True`
- `SSL_CERTFILE`: Path to `certs/cert.pem`
- `SSL_KEYFILE`: Path to `certs/key.pem`
- `DB_PATH`: Path to `data/webhooks.db`
- `WEBHOOK_SECRET`: Optional Bearer token or API key. If empty or `None`, accepts incoming webhooks without authorization requirements.

### `app/database.py`
Thread-safe asynchronous SQLite operations via `aiosqlite`:
- Uses `@asynccontextmanager` pattern (`get_db_connection`) to prevent Python 3.14 thread re-entry errors.
- Pragmas configured:
  - `PRAGMA journal_mode = WAL;` (Write-Ahead Logging for high throughput)
  - `PRAGMA synchronous = NORMAL;` (optimal durability with zero blocking)
  - `PRAGMA busy_timeout = 5000;` (prevents database locking under concurrency)
- Functions: `init_db()`, `save_webhook()`, `get_webhooks()`, `get_webhook_by_id()`, `get_webhook_by_event_id()`, `delete_webhook()`, `clear_all_webhooks()`, `get_stats()`.

### `app/sms_parser.py`
Intelligent payload extractor:
- **Message487:** Detects `life.andre.message487`, `source_name`, `device_code`, and `schema_version`. Extracts `event_id`, `device_code`, `device_id`, `message_type`, `occurred_at`, `channel_id`, `sender`, and `message_body`.
- **Generic Fallback:** Case-insensitive search for sender, recipient, message, and external IDs.

### `app/txn_parser.py`
Bank money-movement SMS parser:
- Extracts the DLT header from the sender ID (`AD-HDFCBK-S` → `HDFCBK`); the 2-letter operator prefix varies per delivery and is ignored.
- Only headers linked in the `banks` table are parsed. `DEFAULT_BANKS` seeds that table the first time it is created.
- `PATTERNS` holds one regex per known message format. Each pattern names its bank in the text, so a bank sending from a new header only needs that header linked on `/settings`.
- Unrecognised formats (FASTag tolls, statements, OTPs) return `None` and get no transaction row.
- **Adding a format:** add a pattern with a sample to `tests/test_txn_parser.py`, deploy, then hit `POST /api/transactions/backfill` (also runs on startup) to parse stored messages.

### `app/gnucash.py` and `app/gnucash_client.py`
The SMS → gnucash-web bridge:
- `gnucash.py` is pure logic: `normalise_merchant` (collapses `AMAZON PAY IN G` → `AMAZON PAY`, drops gateway prefixes, keeps masked UPI), `build_account_paths` (full account paths from `parent_id`), `build_transaction_payload` (IST date, 2-decimal signed splits, cleared card split, `sms-event:` notes tag) and `choose_suggestion` (the ranked "To" account guess).
- `gnucash_client.py` is a thin async `httpx` client for `GET /api/accounts`, `GET /api/transactions` and `POST /api/transactions`. Failures are typed (`GnucashAuthError`, `GnucashNotFound`, `GnucashTimeout`, `GnucashUnreachable`, `GnucashValidationError`) so callers branch on the cause, not on message text.
- `gnucash_post.py` is the posting engine, kept out of the routes so it is testable: account cache, `attempt_post` (claim → submit → store), duplicate detection (±2 days, same signed amount/account, ignores `sms-event:` entries), idempotent retries (adopt by tag before `POST`), and `learn_from_history`. It always releases the `posting` claim, including on unexpected errors.
- The integration is off (no Post buttons) unless `GNUCASH_URL` and `GNUCASH_API_TOKEN` are both set.

### `app/main.py`
FastAPI routes and middleware:
- **`POST /webhook/message487` & `POST /webhook/sms`:**
  - Authenticates via Bearer token (if `WEBHOOK_SECRET` configured).
  - Reads raw body and parses JSON or URL-encoded form data.
  - Passes payload to `parse_sms_payload`.
  - Saves into SQLite database with deduplication.
  - Returns Message487 confirmation format:
    ```json
    {
      "status": "accepted",
      "event_id": "<event_id>",
      "received_at": "<timestamp>",
      "duplicate": false
    }
    ```
- **`/api/webhooks`:** List, search, pagination, and deletion endpoints.
- **`/api/stats`:** Real-time event counts, database file size, and provider breakdown.
- **`/health`:** Service health probe.
- **`/dashboard`:** Serves `templates/dashboard.html`. `/dashboard?webhook=<id>` opens that message's inspector.
- **`/transactions`** and **`/settings`:** Transactions and Settings (banks, card names, gnucash mapping, data) pages (see [Transactions API](#transactions-api)).
- **gnucash bridge routes:** `/api/gnucash/accounts`, `POST /api/transactions/{id}/post`, `POST /api/transactions/post-reviewed`, `POST /api/gnucash/learn` and the per-card mapping `PUT /api/accounts/{header}/{last4}/gnucash` (see [gnucash-web API](#gnucash-web-api)).

### `run.py`
Multi-server async launcher:
- Runs an HTTPS server on port `8443` (using the Let's Encrypt certificate).
- Runs an HTTP fallback server on port `8080`.
- Handled concurrently using `asyncio.gather(https_server.serve(), http_server.serve())`.

---

## 5. Database Schema & SQLite Design

The SQLite database is located at `data/webhooks.db`.

```sql
CREATE TABLE IF NOT EXISTS webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    received_at TEXT NOT NULL,
    occurred_at TEXT,
    source TEXT NOT NULL,
    source_name TEXT,
    provider TEXT,
    device_code TEXT,
    device_id TEXT,
    message_type TEXT,
    channel_id TEXT,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    url TEXT NOT NULL,
    headers TEXT NOT NULL,
    query_params TEXT,
    content_type TEXT,
    sender TEXT,
    recipient TEXT,
    message_body TEXT,
    external_message_id TEXT,
    payload_raw TEXT,
    payload_json TEXT,
    client_ip TEXT,
    response_status INTEGER NOT NULL DEFAULT 200
);

CREATE INDEX IF NOT EXISTS idx_webhooks_event_id ON webhooks(event_id);
CREATE INDEX IF NOT EXISTS idx_webhooks_received_at ON webhooks(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhooks_source ON webhooks(source);
CREATE INDEX IF NOT EXISTS idx_webhooks_sender ON webhooks(sender);
CREATE INDEX IF NOT EXISTS idx_webhooks_device_code ON webhooks(device_code);
CREATE INDEX IF NOT EXISTS idx_webhooks_msg_type ON webhooks(message_type);
CREATE INDEX IF NOT EXISTS idx_webhooks_ext_id ON webhooks(external_message_id);
```

Derived bank tables (full DDL in `app/database.py`):

| Table | Key | Purpose |
| :--- | :--- | :--- |
| `transactions` | `event_id` (unique), `webhook_id` | Parsed debit/credit: `occurred_at`, `bank`, `sender_header`, `direction`, `amount`, `instrument`, `channel`, `account_last4`, `counterparty`, `reference`, `balance`, `balance_type`, `raw_text`, `is_read`, `read_at`, plus gnucash fields `merchant_key`, `from_account_id`, `to_account_id`, `suggestion_reason`, `post_status`, `gnucash_tx_id`, `post_error`, `posted_at`. Kept when raw webhooks are deleted. |
| `banks` | `sender_header` | Linked sender codes → bank name. Bank names are joined at read time, so renames apply to past transactions. |
| `account_names` | `sender_header`, `account_last4` | Friendly card/account names, e.g. `HDFCBK` / `1111` → `HDFC Tata Neu`. |
| `gnucash_account_map` | `sender_header`, `account_last4` | Which gnucash-web account each card posts from. |
| `category_rules` | `merchant_key`, `scope`, `source`, `gnucash_account_id` | Learned "To" choices: `source='confirmed'` from your Posts, `source='learned'` read from gnucash history. `hits` counts repeats. |

### Useful SQLite Queries

```bash
# Query the latest 5 incoming SMS messages
python3 -c "import sqlite3; conn = sqlite3.connect('data/webhooks.db'); print(conn.execute('SELECT id, sender, message_body, received_at FROM webhooks ORDER BY id DESC LIMIT 5').fetchall())"

# Query by sender phone number
python3 -c "import sqlite3; conn = sqlite3.connect('data/webhooks.db'); print(conn.execute('SELECT sender, message_body, occurred_at FROM webhooks WHERE sender = \"+15555550123\"').fetchall())"

# Count messages grouped by device
python3 -c "import sqlite3; conn = sqlite3.connect('data/webhooks.db'); print(conn.execute('SELECT device_code, COUNT(*) FROM webhooks GROUP BY device_code').fetchall())"
```

---

## 6. API Contract & Message487 Protocol

### Incoming Payload Format (from Android Message487)
```json
{
  "schema_version": 1,
  "event_id": "83a5bbd3-c42d-455b-92e3-63eae3b913af",
  "device_id": "124b5e24-3c4b-436c-87df-5b48da430859",
  "device_code": "moto-edge-70-fusion",
  "message_type": "sms",
  "occurred_at": "2026-11-14T09:45:43Z",
  "source": "android",
  "source_name": "Android system",
  "text": "Hello world",
  "sender": "+15555550123",
  "channel_id": "sms"
}
```

### Expected Response Format
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "accepted",
  "event_id": "83a5bbd3-c42d-455b-92e3-63eae3b913af",
  "received_at": "2026-11-14T09:45:48.166059+00:00",
  "duplicate": false
}
```

### Transactions API

| Method & Path | Description |
| :--- | :--- |
| `GET /api/transactions` | Parsed transactions, newest first. Filters: `bank` (name or header), `direction` (`debit`/`credit`), `account` (last 4), `read` (`true`/`false`), `limit`, `offset`. |
| `PUT /api/transactions/{id}/read` | Body `{"read": true}` marks read; `false` marks unread. |
| `POST /api/transactions/backfill` | Parse stored webhooks that have no transaction row yet. |
| `GET /api/banks` | Linked sender codes with transaction counts. |
| `PUT /api/banks/{header}` | Body `{"name": "HDFC Bank"}` links or renames a code, then backfills. |
| `DELETE /api/banks/{header}` | Unlink a code (existing transactions are kept). |
| `GET /api/senders` | Sender codes seen in messages, with linked bank (or `null`). |
| `GET /api/accounts` | Cards/accounts seen in transactions with names and counts. |
| `PUT /api/accounts/{header}/{last4}` | Body `{"name": "HDFC Tata Neu"}`; an empty name clears it. |

### gnucash-web API

Enabled only when `GNUCASH_URL` and `GNUCASH_API_TOKEN` are set.

| Method & Path | Description |
| :--- | :--- |
| `GET /api/gnucash/accounts` | Postable gnucash accounts — **leaf accounts only** (no children; their names are unique) and never placeholders/hidden. The pickers show and search the **leaf name**; `path` is returned for tooltips. `?refresh=true` bypasses the cache. |
| `PUT /api/accounts/{header}/{last4}/gnucash` | Body `{"gnucash_account_id": "<uuid>"}` maps a card to its gnucash "From" account; an empty id clears it. |
| `PUT /api/transactions/{id}/to` | Body `{"to_account_id": "<uuid>"}` saves the chosen To without posting; status becomes `suggested`. An empty id clears it. A posted row is refused with `409`. |
| `POST /api/transactions/{id}/post` | Body `{"to_account_id": "<uuid>", "force": false}`. Validates, claims the row and posts it, storing the gnucash id. A row already posted is refused with `409` (it is final). A likely hand-entered duplicate returns `409` with its description/date until you retry with `force: true`. |
| `POST /api/transactions/post-reviewed` | Body `{"posts": [{"id": 1, "to_account_id": "<uuid>"}], "force": false}`. An empty list is rejected; each row posts the To the user actually chose. Returns a per-row result. |
| `POST /api/gnucash/learn` | Reads gnucash history and records merchant → account hints for the suggestion engine. |
| `GET /api/gnucash/rules` | The confirmed and learned rules and each card's usual destination (debugging/visibility). |

### Cards API

| Method & Path | Description |
| :--- | :--- |
| `GET /api/cards` | Each credit card with the latest statement's `total_due`, `min_due`, `due_date` and `paid` flag. |
| `PUT /api/cards/{header}/{last4}/paid` | Body `{"paid": true}` marks the latest statement paid (or unpaid). A paid bill still appears, showing its amount. |

### Data API
| Method & Path | Description |
| :--- | :--- |
| `GET /api/export` | Download all data (every table) as a JSON attachment. |
| `POST /api/import?mode=merge\|replace` | Import an export. `merge` (default) keeps existing rows and skips duplicates; `replace` wipes every table first. Body is the export JSON (envelope with `tables`, or a bare `{table: rows}` map). |
| `DELETE /api/data` | Delete all data and re-seed the default bank sender codes (fresh-install state). |

A row whose card has no mapping, or whose "To" is **Uncategorised**, cannot be posted (the Post button is disabled); sms_webhook never creates accounts in gnucash-web. Transaction rows expose `in_ledger` (true when `gnucash_tx_id` is set), and the Post endpoint returns `409` for a busy or deleted row, `502` for gnucash/token failures, and `400` for validation problems.

---

## 7. Operational Setup, Docker & Podman Deployment

### Running with Podman / Docker (Recommended)

The project includes a production-grade `Dockerfile`, `.dockerignore`, and `docker-compose.yml` preconfigured with SELinux volume labels (`:Z`) and health checks:

```bash
# Build the image
cd ~/sms_webhook
podman build -t sms-webhook:latest .

# Run the container (with persistent host volumes for data and certs)
podman run -d \
  --name sms-webhook \
  --restart unless-stopped \
  -p 8443:8443 \
  -p 8080:8080 \
  -v ./data:/app/data:Z \
  -v ./certs:/app/certs:Z \
  -e HOST=0.0.0.0 \
  -e PORT=8443 \
  -e HTTPS_ENABLED=true \
  -e AUTO_GENERATE_CERT=false \
  -e SSL_CERTFILE=/app/certs/cert.pem \
  -e SSL_KEYFILE=/app/certs/key.pem \
  -e DB_PATH=/app/data/webhooks.db \
  localhost/sms-webhook:latest

# Or using podman-compose / docker-compose:
podman-compose up -d
```

### Checking Container Health and Logs
```bash
podman ps --filter "name=sms-webhook"
podman logs -f sms-webhook
```

### Native Host Execution (Without Containers)
```bash
cd ~/sms_webhook
./venv/bin/python run.py --port 8443
```

### 24/7 Autostart via Systemd (User Service)
A preconfigured systemd unit file is provided at `sms_webhook.service`:

```bash
# 1. Create user systemd directory
mkdir -p ~/.config/systemd/user/

# 2. Copy unit file
cp ~/sms_webhook/sms_webhook.service ~/.config/systemd/user/

# 3. Reload daemon and enable service
systemctl --user daemon-reload
systemctl --user enable --now sms_webhook.service

# 4. Check status and logs
systemctl --user status sms_webhook.service
journalctl --user -u sms_webhook.service -f
```

---

## 8. Certificate Lifecycle & Renewal

The certificate is issued by Let's Encrypt for `sms.example.com` and is valid for 90 days.

### Manual or Automated Renewal
Certbot is installed inside `~/sms_webhook/venv/bin/certbot` with the Cloudflare DNS plugin.

To renew the certificate:
```bash
cd ~/sms_webhook
./venv/bin/certbot renew \
  --config-dir certs/letsencrypt/config \
  --work-dir certs/letsencrypt/work \
  --logs-dir certs/letsencrypt/logs

# Copy renewed certificates
cp -f certs/letsencrypt/config/live/sms.example.com/fullchain.pem certs/cert.pem
cp -f certs/letsencrypt/config/live/sms.example.com/privkey.pem certs/key.pem

# Restart service if running under systemd
systemctl --user restart sms_webhook.service
```

---

## 9. Testing & Verification

The test suite includes automated unit and integration tests covering database lifecycle, idempotency, SMS extraction, HTTP endpoints, and the gnucash-web bridge.

```bash
cd ~/sms_webhook
./venv/bin/pytest -v
```

### Lint, types and the pre-push hook

`ruff` (lint) and `mypy` (types) are configured in `pyproject.toml` and installed with
`requirements-dev.txt`. A `pre-push` hook runs ruff, mypy and pytest before every push:

```bash
git config core.hooksPath githooks   # enable it once per clone
./venv/bin/ruff check app tests run.py
./venv/bin/mypy app                  # strict (configured in pyproject.toml)
./githooks/pre-push                  # or just `git push`
```

### Test Coverage:
1. `tests/test_database.py`: Tests table schema, WAL pragma, insert, deduplication on duplicate `event_id`, query by ID, and stats aggregation.
2. `tests/test_sms_parsing.py`: Tests payload parsing for Message487 SMS, Message487 notifications, and generic payloads.
3. `tests/test_message487_payload.py`: Integration test verifying the exact JSON structure sent by `moto-edge-70-fusion`.
4. `tests/test_txn_parser.py`: Bank transaction parser tests over anonymised real samples.
5. `tests/test_gnucash.py`: Merchant normaliser, IST date handling, account paths, post-payload signs/rounding/truncation, suggestion order, and gnucash client error mapping (through `httpx.MockTransport`).
6. `tests/test_gnucash_post.py`: Posting-engine idempotency (create/`PUT`/adopt-by-tag/timeout), duplicate warning and `force`, deleted-in-gnucash, claim release on unexpected errors, and the bulk/`/to` endpoints (through `httpx.MockTransport` and `ASGITransport`).
7. `tests/test_webhook_api.py`: Tests FastAPI routes with Bearer authentication and header inspection.

---

## 10. Docker & Podman Compose Production Deployment

The project is fully containerized and published on Docker Hub as [`docker.io/kishansl/sms-webhook:1.6.4`](https://hub.docker.com/r/kishansl/sms-webhook).

### Production `docker-compose.yml`

```yaml
version: '3.8'

services:
  sms-webhook:
    image: docker.io/kishansl/sms-webhook:latest
    container_name: sms-webhook
    restart: always

    # Map ports:
    # 8443: Direct HTTPS (or change to 443:8443 for standard HTTPS port)
    # 8080: Optional plain HTTP (useful if running behind Traefik, Nginx, or Caddy)
    ports:
      - "8443:8443"
      - "8080:8080"

    # Persistent host storage (:Z enables SELinux compatibility for Podman/Fedora/RHEL)
    volumes:
      - ./data:/app/data:Z
      - ./certs:/app/certs:Z

    environment:
      - HOST=0.0.0.0
      - PORT=8443
      - HTTPS_ENABLED=true
      - AUTO_GENERATE_CERT=false
      - SSL_CERTFILE=/app/certs/cert.pem
      - SSL_KEYFILE=/app/certs/key.pem
      - DB_PATH=/app/data/webhooks.db
      - TZ=Asia/Kolkata
      # Set a secret token for authentication (Header: 'Authorization: Bearer <secret>')
      # - WEBHOOK_SECRET=change_me_to_a_secure_token

    # Production container health monitoring
    healthcheck:
      test: ["CMD-SHELL", "curl -f -k https://127.0.0.1:8443/health || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

    # Security hardening
    security_opt:
      - no-new-privileges:true

    # Resource limits to protect host resources
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          memory: 128M

    # Prevent container logs from consuming excessive disk space
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### Running in Production

#### With Docker Compose:
```bash
# 1. Start in detached background mode
docker compose up -d

# 2. View live logs
docker compose logs -f

# 3. Check container health status
docker compose ps
```

#### With Podman Compose / Rootless Podman:
```bash
# 1. Start with podman-compose
podman-compose up -d

# Or run directly with Podman:
podman run -d --name sms-webhook \
  --restart always \
  -p 8443:8443 -p 8080:8080 \
  -v ./data:/app/data:Z \
  -v ./certs:/app/certs:Z \
  -e SSL_CERTFILE=/app/certs/cert.pem \
  -e SSL_KEYFILE=/app/certs/key.pem \
  -e DB_PATH=/app/data/webhooks.db \
  docker.io/kishansl/sms-webhook:1.6.4
```

---

## 11. Git Repository & Workflow

### Cloning & Fresh Setup
```bash
# 1. Clone the repository
git clone https://github.com/<your-user>/sms_webhook.git
cd sms_webhook

# 2. Setup environment configuration
cp .env.example .env

# 3. Create persistent directories
mkdir -p data certs

# 4. Create virtual environment and install dependencies
python3 -m venv venv
./venv/bin/pip install -r requirements-dev.txt

# 5. Run tests to verify setup
./venv/bin/pytest -v
```

### Git Security & Excluded Files (.gitignore)
To safeguard privacy and credentials, the repository enforces a strict `.gitignore` policy. The following files are **strictly excluded** from version control:

| Excluded Pattern | Reason & Privacy Protection |
| :--- | :--- |
| `data/*.db*` | SQLite databases containing sensitive SMS messages, OTPs, and personal phone numbers. |
| `certs/` & `*.pem` | SSL private keys (`key.pem`) and Let's Encrypt certificates. |
| `.env` | Local deployment secrets and auth bearer tokens. |
| `cloudflare.ini` | Cloudflare DNS API tokens used during Let's Encrypt challenge. |
| `cloudflared` | Local architecture-specific executable binaries. |
| `venv/`, `__pycache__/` | Python virtual environments and bytecode cache files. |

> [!IMPORTANT]
> Always verify that `git status` does not show untracked database files (`data/webhooks.db`) or private keys (`certs/key.pem`) before committing.
