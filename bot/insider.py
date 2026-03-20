"""SEC Form 4 insider trading activity via EDGAR full-text search.

Endpoint:
  https://efts.sec.gov/LATEST/search-index?q="TICKER"&forms=4
  &dateRange=custom&startdt=DATE&enddt=DATE

We issue two queries:
  1. TICKER + "P - Purchase"  → counts insider buy transactions
  2. TICKER + "S - Sale"      → counts insider sell transactions

These phrases appear verbatim in Form 4 XML transaction-type fields
and are indexed by the EDGAR full-text search engine.

Signal rules:
  buy_count > sell_count AND buy_count >= 2 → BUY
  sell_count > buy_count AND sell_count >= 2 → SELL
  else                                        → NONE

Cached per ticker for 6 hours. Free, no API key required.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "InsiderResult"]] = {}
_CACHE_TTL = 21_600  # 6 hours

_EDGAR_BASE = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%22{ticker}%22+%22{phrase}%22"
    "&forms=4"
    "&dateRange=custom"
    "&startdt={start}"
    "&enddt={end}"
)
_HEADERS = {"User-Agent": "StockBotAI/1.0 research@stockbot.ai"}


@dataclass
class InsiderResult:
    ticker: str
    buy_count: int = 0
    sell_count: int = 0
    signal: str = "NONE"          # BUY / SELL / NONE
    summary: str = "No recent insider Form 4 activity"
    error: str = ""


def _count_hits(url: str) -> int:
    """Return total hit count from an EDGAR full-text search URL, or 0 on error."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        return int(resp.json().get("hits", {}).get("total", {}).get("value", 0))
    except Exception as exc:
        log.debug("EDGAR count fetch failed: %s", exc)
        return 0


def get_insider_activity(ticker: str, days: int = 14) -> InsiderResult:
    """Fetch SEC Form 4 insider buy/sell counts for *ticker* in the last *days* days.

    Returns a neutral InsiderResult on any error — never raises.
    Cached per ticker for 6 hours.
    """
    now = time.time()

    if ticker in _CACHE:
        ts, cached = _CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return cached

    try:
        end_dt   = datetime.utcnow()
        start_dt = end_dt - timedelta(days=days)
        start    = start_dt.strftime("%Y-%m-%d")
        end      = end_dt.strftime("%Y-%m-%d")

        buy_url  = _EDGAR_BASE.format(ticker=ticker, phrase="P+-+Purchase", start=start, end=end)
        sell_url = _EDGAR_BASE.format(ticker=ticker, phrase="S+-+Sale",     start=start, end=end)

        buy_count  = _count_hits(buy_url)
        sell_count = _count_hits(sell_url)

        if buy_count > sell_count and buy_count >= 2:
            signal  = "BUY"
            summary = (
                f"Insiders net BUYING: {buy_count} purchases vs {sell_count} sales "
                f"in last {days} days"
            )
        elif sell_count > buy_count and sell_count >= 2:
            signal  = "SELL"
            summary = (
                f"Insiders net SELLING: {sell_count} sales vs {buy_count} purchases "
                f"in last {days} days"
            )
        else:
            signal  = "NONE"
            summary = (
                f"No clear insider signal: {buy_count} purchases, {sell_count} sales "
                f"in last {days} days"
            )

        result = InsiderResult(
            ticker=ticker,
            buy_count=buy_count,
            sell_count=sell_count,
            signal=signal,
            summary=summary,
        )
        _CACHE[ticker] = (now, result)
        log.info(
            "Insider %s: buys=%d sells=%d → %s",
            ticker, buy_count, sell_count, signal,
        )
        return result

    except Exception as exc:
        log.warning("Insider fetch failed for %s: %s", ticker, exc)
        result = InsiderResult(ticker=ticker, error=str(exc))
        _CACHE[ticker] = (now, result)
        return result
