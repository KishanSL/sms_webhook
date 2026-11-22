"""
Thin async client for the gnucash-web ledger API.

All calls use `Authorization: Bearer gcw_…` (an API token created in gnucash-web).
The four endpoints used are wrapped by the methods below.

HTTP failures are turned into typed `GnucashError` subclasses so callers can branch on
the kind of failure instead of matching message text.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


class GnucashError(Exception):
    """Base class for gnucash-web failures; the message is meant for the user."""


class GnucashAuthError(GnucashError):
    """The API token was rejected (401/403)."""


class GnucashNotFound(GnucashError):
    """The requested transaction or account does not exist in gnucash-web (404)."""


class GnucashTimeout(GnucashError):
    """The request timed out: the outcome is unknown and must be verified by tag."""


class GnucashUnreachable(GnucashError):
    """The request never reached gnucash-web (connection refused / DNS / reset)."""


class GnucashValidationError(GnucashError):
    """gnucash-web rejected the payload (4xx other than auth/404)."""


class GnucashClient:
    def __init__(self, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.request(method, url, headers=self._headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise GnucashTimeout("gnucash-web did not respond in time") from exc
        except httpx.HTTPError as exc:
            raise GnucashUnreachable(f"Could not reach gnucash-web: {exc}") from exc

        if response.status_code in (401, 403):
            raise GnucashAuthError("gnucash-web rejected the API token")
        if response.status_code == 404:
            raise GnucashNotFound("Not found in gnucash-web (it may have been deleted there)")
        if response.status_code >= 400:
            raise GnucashValidationError(self._error_text(response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GnucashError("gnucash-web returned a malformed response") from exc

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return f"gnucash-web returned HTTP {response.status_code}"
        if isinstance(body, dict):
            detail = body.get("error") or body.get("detail") or body.get("message")
            if isinstance(detail, str) and detail:
                return detail
            if isinstance(detail, list) and detail:
                return "; ".join(str(d) for d in detail)
        return f"gnucash-web returned HTTP {response.status_code}"

    # --- Endpoints ---

    async def list_accounts(self) -> list[dict[str, Any]]:
        """GET /api/accounts — the full account list (placeholders and hidden included)."""
        return await self._request("GET", "/api/accounts") or []

    async def list_transactions(
        self,
        search: str | None = None,
        account_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """GET /api/transactions — newest first, with splits. Used for learn and duplicate checks."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        if account_id:
            params["account_id"] = account_id
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date
        return await self._request("GET", "/api/transactions", params=params) or []

    async def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/transactions — post a balanced transaction, returns it with its id."""
        return await self._request("POST", "/api/transactions", json=payload) or {}
