"""Options flow analysis using yfinance.

Fetches the nearest-expiry options chain and computes:
  - Put/Call volume ratio
  - Unusual volume flag (total options volume ≥ 3× 30-day avg stock volume)

Signal rules:
  put_call_ratio < 0.7  → BULLISH  (heavy call buying = smart money bullish)
  put_call_ratio > 1.3  → BEARISH  (heavy put buying = smart money hedging)
  0.7 – 1.3             → NEUTRAL

Unusual volume at the BULLISH end is a conviction multiplier.

Cached per ticker for 1 hour. Requires yfinance (already installed).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "OptionsResult"]] = {}
_CACHE_TTL = 3600  # seconds


@dataclass
class OptionsResult:
    ticker: str
    put_call_ratio: float | None = None
    signal: str = "NEUTRAL"          # BULLISH / BEARISH / NEUTRAL
    unusual_volume: bool = False      # total options vol ≥ 3× 20-day avg stock vol
    call_volume: int = 0
    put_volume: int = 0
    error: str = ""


def get_options_flow(ticker: str) -> OptionsResult:
    """Fetch options chain and compute put/call ratio.

    Returns a neutral OptionsResult on any error — never raises.
    """
    now = time.time()

    if ticker in _CACHE:
        ts, cached = _CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return cached

    try:
        import yfinance as yf  # type: ignore[import]

        t           = yf.Ticker(ticker)
        expirations = t.options

        if not expirations:
            result = OptionsResult(ticker=ticker, error="No options chain available")
            _CACHE[ticker] = (now, result)
            return result

        # Use the nearest expiration date
        chain = t.option_chain(expirations[0])
        call_vol = int(chain.calls["volume"].fillna(0).sum())
        put_vol  = int(chain.puts["volume"].fillna(0).sum())

        if call_vol == 0:
            result = OptionsResult(ticker=ticker, error="Zero call volume")
            _CACHE[ticker] = (now, result)
            return result

        pc_ratio = round(put_vol / call_vol, 3)

        if pc_ratio < 0.7:
            signal = "BULLISH"
        elif pc_ratio > 1.3:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        # Unusual volume: total options vol ≥ 3× 30-day avg equity volume
        unusual = False
        try:
            hist    = t.history(period="3mo", interval="1d")
            # Use only last 30 trading days for the average
            avg_vol = float(hist["Volume"].iloc[-30:].mean()) if len(hist) >= 30 else (
                float(hist["Volume"].mean()) if not hist.empty else 0.0
            )
            if avg_vol > 0:
                unusual = (call_vol + put_vol) >= avg_vol * 3
        except Exception:
            pass

        result = OptionsResult(
            ticker=ticker,
            put_call_ratio=pc_ratio,
            signal=signal,
            unusual_volume=unusual,
            call_volume=call_vol,
            put_volume=put_vol,
        )
        _CACHE[ticker] = (now, result)
        log.info(
            "Options %s: P/C=%.2f signal=%s unusual=%s (calls=%d puts=%d)",
            ticker, pc_ratio, signal, unusual, call_vol, put_vol,
        )
        return result

    except Exception as exc:
        log.warning("Options fetch failed for %s: %s", ticker, exc)
        result = OptionsResult(ticker=ticker, error=str(exc))
        _CACHE[ticker] = (now, result)
        return result
