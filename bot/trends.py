"""Google Trends scanner using pytrends.

Fetches the 14-day interest-over-time series for a ticker on Google
and computes a week-over-week change.

Signal:
  wow_change_pct > 20%   → fomo_signal = True  (retail FOMO building)
  current_score 0-100    → raw Google Trends interest (relative, not absolute)

This is a supporting / retail-momentum signal only.
It is NOT a buy gate — enabled optionally via ENABLE_TRENDS=true.

Requires: pip install pytrends
Cached per ticker for 1 hour (also helps avoid Google rate-limiting).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "TrendsResult"]] = {}
_CACHE_TTL = 3600  # seconds


@dataclass
class TrendsResult:
    ticker: str
    current_score: float = 50.0     # last-7-day average Google Trends score (0-100)
    wow_change_pct: float = 0.0     # week-over-week change in percent
    fomo_signal: bool = False        # wow_change_pct > 20%
    error: str = ""


def get_trends_score(ticker: str) -> TrendsResult:
    """Fetch 14-day Google Trends data for *ticker* and compute WoW change.

    Returns a neutral TrendsResult on any error — never raises.
    """
    now = time.time()

    if ticker in _CACHE:
        ts, cached = _CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return cached

    try:
        from pytrends.request import TrendReq  # type: ignore[import]

        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        pytrends.build_payload([ticker], cat=0, timeframe="now 14-d", geo="US")
        data = pytrends.interest_over_time()

        if data is None or data.empty or ticker not in data.columns:
            result = TrendsResult(ticker=ticker, error="No trend data returned")
            _CACHE[ticker] = (now, result)
            return result

        series = data[ticker].dropna()
        if len(series) < 14:
            result = TrendsResult(ticker=ticker, error="Insufficient data points")
            _CACHE[ticker] = (now, result)
            return result

        last_7  = float(series.iloc[-7:].mean())
        prior_7 = float(series.iloc[-14:-7].mean())
        wow_change = ((last_7 - prior_7) / prior_7 * 100.0) if prior_7 > 0 else 0.0

        result = TrendsResult(
            ticker=ticker,
            current_score=round(last_7, 1),
            wow_change_pct=round(wow_change, 1),
            fomo_signal=wow_change > 20.0,
        )
        _CACHE[ticker] = (now, result)
        log.info(
            "Trends %s: score=%.1f WoW=%+.1f%% fomo=%s",
            ticker, last_7, wow_change, result.fomo_signal,
        )
        return result

    except Exception as exc:
        log.warning("Trends fetch failed for %s: %s", ticker, exc)
        result = TrendsResult(ticker=ticker, error=str(exc))
        _CACHE[ticker] = (now, result)
        return result
