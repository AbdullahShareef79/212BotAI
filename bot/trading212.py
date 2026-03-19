"""Trading 212 REST API client.

Docs: https://t212public-api-docs.redoc.ly/
Supports both *demo* (practice) and *live* environments.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Trading 212 rate limits: 1 req / 5 s for most endpoints
_MIN_INTERVAL = 5.2  # seconds between calls (with safety margin)


class Trading212Error(Exception):
    """Raised on non-2xx responses from Trading 212."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


class Trading212Client:
    """Thin wrapper around the Trading 212 equity API."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": api_key}
        self._last_call = 0.0
        self._client = httpx.Client(timeout=30, headers=self._headers)

    # ── helpers ──────────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._throttle()
        url = f"{self._base}{path}"
        log.debug("%s %s", method, url)
        resp = self._client.request(method, url, **kwargs)
        if resp.status_code not in (200, 201, 204):
            raise Trading212Error(resp.status_code, resp.text)
        if resp.status_code == 204:
            return None
        return resp.json()

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict | None = None) -> Any:
        return self._request("POST", path, json=body)

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ── instruments ──────────────────────────────────────────

    def get_instruments(self) -> list[dict]:
        """Return all tradeable instruments."""
        return self._get("/api/v0/equity/metadata/instruments")

    def get_exchange_list(self) -> list[dict]:
        """Return available exchanges."""
        return self._get("/api/v0/equity/metadata/exchanges")

    # ── account ──────────────────────────────────────────────

    def get_account_cash(self) -> dict:
        """Return free / total / invested cash info."""
        return self._get("/api/v0/equity/account/cash")

    def get_portfolio(self) -> list[dict]:
        """Return all open positions."""
        return self._get("/api/v0/equity/portfolio")

    def get_position(self, ticker: str) -> dict | None:
        """Return position for a single ticker, or None."""
        try:
            return self._get(f"/api/v0/equity/portfolio/{ticker}")
        except Trading212Error as exc:
            if exc.status == 404:
                return None
            raise

    # ── orders ───────────────────────────────────────────────

    def place_market_order(self, ticker: str, quantity: float) -> dict:
        """Place a market BUY order (quantity > 0) or SELL (quantity < 0)."""
        body = {"ticker": ticker, "quantity": quantity}
        log.info("MARKET ORDER  %s  qty=%.4f", ticker, quantity)
        return self._post("/api/v0/equity/orders/market", body)

    def place_value_order(self, ticker: str, value: float) -> dict:
        """Place a market order by EUR value (fractional shares)."""
        body = {"ticker": ticker, "value": value}
        log.info("VALUE ORDER   %s  €%.2f", ticker, value)
        return self._post("/api/v0/equity/orders/market", body)

    def place_limit_order(
        self,
        ticker: str,
        quantity: float,
        limit_price: float,
        time_validity: str = "DAY",
    ) -> dict:
        body = {
            "ticker": ticker,
            "quantity": quantity,
            "limitPrice": limit_price,
            "timeValidity": time_validity,
        }
        log.info("LIMIT ORDER   %s  qty=%.4f @%.2f", ticker, quantity, limit_price)
        return self._post("/api/v0/equity/orders/limit", body)

    def place_stop_order(
        self,
        ticker: str,
        quantity: float,
        stop_price: float,
        time_validity: str = "DAY",
    ) -> dict:
        body = {
            "ticker": ticker,
            "quantity": quantity,
            "stopPrice": stop_price,
            "timeValidity": time_validity,
        }
        log.info("STOP ORDER    %s  qty=%.4f @%.2f", ticker, quantity, stop_price)
        return self._post("/api/v0/equity/orders/stop", body)

    def get_orders(self) -> list[dict]:
        """Return all pending (unfilled) orders."""
        return self._get("/api/v0/equity/orders")

    def cancel_order(self, order_id: int) -> None:
        self._delete(f"/api/v0/equity/orders/{order_id}")

    # ── history ──────────────────────────────────────────────

    def get_order_history(self, cursor: int | None = None, limit: int = 50) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get("/api/v0/equity/history/orders", **params)

    def get_dividend_history(self, cursor: int | None = None, limit: int = 50) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get("/api/v0/equity/history/dividends", **params)

    # ── pies (auto-invest) ───────────────────────────────────

    def get_pies(self) -> list[dict]:
        return self._get("/api/v0/equity/pies")

    # ── convenience ──────────────────────────────────────────

    def close(self) -> None:
        self._client.close()
