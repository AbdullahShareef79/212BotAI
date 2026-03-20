"""Trading 212 REST API client.

Docs: https://t212public-api-docs.redoc.ly/
Supports both *demo* (practice) and *live* environments.

Authentication: HTTP Basic Auth – Authorization: Basic base64(API_KEY:API_SECRET)
Ticker format:  T212 uses MSFT_US_EQ, not MSFT.  _to_t212() converts
                on the fly using the instruments endpoint cache.
Value orders:   Not supported by API – place_value_order fetches the live
                price via yfinance and converts to a quantity order.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx
import yfinance as yf

log = logging.getLogger(__name__)

# Fallback floor between requests when no rate-limit headers are present
_MIN_INTERVAL = 1.2  # seconds


class Trading212Error(Exception):
    """Raised on non-2xx responses from Trading 212."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


class Trading212Client:
    """Thin wrapper around the Trading 212 equity API."""

    def __init__(self, api_key: str, base_url: str, api_secret: str = "") -> None:
        self._base = base_url.rstrip("/")

        # HTTP Basic Auth: base64(API_KEY:API_SECRET)
        credentials = f"{api_key}:{api_secret}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        self._headers = {"Authorization": f"Basic {encoded}"}
        self._last_call = 0.0
        self._rate_reset: float = 0.0   # Unix ts when limit resets
        self._rate_remaining: int = 999 # requests left in current window
        self._client = httpx.Client(timeout=30, headers=self._headers)
        self._instrument_map: dict[str, str] = {}  # "MSFT" → "MSFT_US_EQ"

    # ── ticker helpers ───────────────────────────────────────────

    def _load_instrument_map(self) -> None:
        """Fetch all instruments once and build yf-symbol → T212-ticker map.

        Prefers US equity listings (suffix _US_EQ).  Falls back to the first
        match when no US listing exists.
        """
        if self._instrument_map:
            return  # already loaded
        try:
            instruments = self.get_instruments()
            tmp: dict[str, str] = {}
            for inst in instruments:
                t212 = inst.get("ticker", "")
                # Derive the plain symbol: everything before the first "_"
                symbol = t212.split("_")[0]
                if not symbol:
                    continue
                # Prefer US equity listings
                if t212.endswith("_US_EQ"):
                    tmp[symbol] = t212
                elif symbol not in tmp:
                    tmp[symbol] = t212
            self._instrument_map = tmp
            log.info("Loaded %d instrument mappings from T212", len(tmp))
        except Exception as exc:
            log.warning("Could not load instrument map: %s", exc)

    def _to_t212(self, ticker: str) -> str:
        """Convert a plain yfinance symbol (MSFT) to a T212 ticker (MSFT_US_EQ)."""
        # If already in T212 format, return as-is
        if "_" in ticker:
            return ticker
        self._load_instrument_map()
        t212 = self._instrument_map.get(ticker.upper())
        if t212:
            return t212
        # Fallback: append _US_EQ and hope for the best
        fallback = f"{ticker.upper()}_US_EQ"
        log.warning("No T212 mapping for %s – using fallback %s", ticker, fallback)
        return fallback

    # ── helpers ──────────────────────────────────────────────

    def _throttle(self) -> None:
        """Respect rate-limit headers; fall back to _MIN_INTERVAL floor."""
        now = time.time()

        # If the window is exhausted, wait until it resets
        if self._rate_remaining <= 0 and self._rate_reset > now:
            wait = self._rate_reset - now + 0.1
            log.info("Rate limit exhausted – waiting %.1fs for reset", wait)
            time.sleep(wait)

        # Always keep a minimum gap between calls
        elapsed = time.time() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

        self._last_call = time.time()

    def _update_rate_headers(self, resp: httpx.Response) -> None:
        """Parse x-ratelimit-* response headers for adaptive throttling."""
        try:
            remaining = resp.headers.get("x-ratelimit-remaining")
            reset_ts = resp.headers.get("x-ratelimit-reset")
            if remaining is not None:
                self._rate_remaining = int(remaining)
            if reset_ts is not None:
                self._rate_reset = float(reset_ts)
        except Exception:
            pass

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._throttle()
        url = f"{self._base}{path}"
        log.debug("%s %s", method, url)
        resp = self._client.request(method, url, **kwargs)
        self._update_rate_headers(resp)
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
        t212_ticker = self._to_t212(ticker)
        try:
            return self._get(f"/api/v0/equity/portfolio/{t212_ticker}")
        except Trading212Error as exc:
            if exc.status == 404:
                return None
            raise

    # ── orders ───────────────────────────────────────────────

    def place_market_order(self, ticker: str, quantity: float) -> dict:
        """Place a market BUY order (quantity > 0) or SELL (quantity < 0)."""
        t212_ticker = self._to_t212(ticker)
        body = {"ticker": t212_ticker, "quantity": quantity}
        log.info("MARKET ORDER  %s (%s)  qty=%.6f", ticker, t212_ticker, quantity)
        return self._post("/api/v0/equity/orders/market", body)

    def place_value_order(self, ticker: str, value: float) -> dict:
        """Place a market order by EUR value – converts to quantity via yfinance.

        The T212 API does not support value orders; we fetch the live price
        from yfinance and submit a quantity-based market order instead.
        """
        try:
            price = yf.Ticker(ticker).fast_info.last_price
            if not price or price <= 0:
                raise ValueError(f"Invalid price {price} for {ticker}")
        except Exception as exc:
            raise Trading212Error(0, f"Could not fetch price for {ticker}: {exc}") from exc

        quantity = round(value / price, 2)
        if quantity <= 0:
            raise Trading212Error(0, f"Computed quantity {quantity} <= 0 for {ticker} at {price}")

        t212_ticker = self._to_t212(ticker)
        body = {"ticker": t212_ticker, "quantity": quantity}
        log.info(
            "VALUE→QTY ORDER  %s (%s)  €%.2f = %.6f shares @%.4f",
            ticker, t212_ticker, value, quantity, price,
        )
        return self._post("/api/v0/equity/orders/market", body)

    def place_limit_order(
        self,
        ticker: str,
        quantity: float,
        limit_price: float,
        time_validity: str = "DAY",
    ) -> dict:
        t212_ticker = self._to_t212(ticker)
        body = {
            "ticker": t212_ticker,
            "quantity": quantity,
            "limitPrice": limit_price,
            "timeValidity": time_validity,
        }
        log.info("LIMIT ORDER   %s (%s)  qty=%.6f @%.2f", ticker, t212_ticker, quantity, limit_price)
        return self._post("/api/v0/equity/orders/limit", body)

    def place_stop_order(
        self,
        ticker: str,
        quantity: float,
        stop_price: float,
        time_validity: str = "DAY",
    ) -> dict:
        t212_ticker = self._to_t212(ticker)
        body = {
            "ticker": t212_ticker,
            "quantity": quantity,
            "stopPrice": stop_price,
            "timeValidity": time_validity,
        }
        log.info("STOP ORDER    %s (%s)  qty=%.6f @%.2f", ticker, t212_ticker, quantity, stop_price)
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
