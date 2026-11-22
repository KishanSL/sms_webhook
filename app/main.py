"""
Homelab HTTPS SMS Webhook Service.

This module assembles the FastAPI app from the routers in `app/routers` and runs the startup
lifespan. Route handlers live in the routers; shared helpers live in `app/deps.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, reset_interrupted_posts
from app.deps import backfill_transactions
from app.routers import accounts, cards, data, gnucash, pages, system, transactions, webhooks

# run.py serves this app from two uvicorn servers (HTTPS + HTTP) in one process, and each
# runs the lifespan. Startup must run once, or concurrent schema setup hits "database is locked".
_startup_lock = asyncio.Lock()
_startup_done = False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifecycle manager: initialize database and certificates on startup (once per process)."""
    global _startup_done
    async with _startup_lock:
        if not _startup_done:
            await init_db(settings.DB_PATH)
            await reset_interrupted_posts(settings.DB_PATH)
            await backfill_transactions()

            _startup_done = True

    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhooks.router)
app.include_router(data.router)
app.include_router(transactions.router)
app.include_router(cards.router)
app.include_router(gnucash.router)
app.include_router(accounts.router)
app.include_router(system.router)
app.include_router(pages.router)

# Static assets (page JavaScript) served from /static.
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")), name="static")
