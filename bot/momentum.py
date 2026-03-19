"""Momentum scanner – detects explosive movers for the aggressive strategy.

Scans for:
  • Stocks up >5% on the day with 3x+ average volume (unusual activity)
  • 52-week high breakouts (new-high momentum)
  • Volume-weighted ranking for priority AI analysis
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

# ── configurable thresholds (overridden by config) ──────────
_DEFAULT_MIN_GAIN_PCT = 5.0
_DEFAULT_VOL_MULT = 3.0
_DEFAULT_LOOKBACK_VOL_DAYS = 20


@dataclass
class MomentumSignal:
    """Result of a momentum scan for a single ticker."""
    ticker: str
    daily_gain_pct: float       # today's % gain
    volume_ratio: float         # current volume / 20-day avg
    is_52w_high: bool           # at or within 2% of 52-week high
    high_52w: float             # 52-week high price
    current_price: float
    avg_volume: float
    current_volume: float
    momentum_score: float       # composite score 0-100
    gap_up_pct: float = 0.0    # gap from prev close to today's open
    consecutive_green: int = 0  # days of consecutive gains
    rsi_14: float = 0.0        # current RSI for context

    @property
    def is_strong_momentum(self) -> bool:
        """Score >= 70 indicates strong momentum worth analyzing."""
        return self.momentum_score >= 70

    @property
    def tier(self) -> str:
        if self.momentum_score >= 85:
            return "S"  # super-momentum
        elif self.momentum_score >= 70:
            return "A"
        elif self.momentum_score >= 50:
            return "B"
        return "C"


def scan_momentum(
    tickers: list[str],
    min_gain_pct: float = _DEFAULT_MIN_GAIN_PCT,
    vol_multiplier: float = _DEFAULT_VOL_MULT,
) -> list[MomentumSignal]:
    """Scan a list of tickers for momentum signals.

    Returns only tickers that pass at least one momentum filter,
    sorted by momentum_score descending.
    """
    signals: list[MomentumSignal] = []

    for ticker in tickers:
        try:
            sig = _analyze_ticker_momentum(ticker, min_gain_pct, vol_multiplier)
            if sig:
                signals.append(sig)
        except Exception as exc:
            log.debug("Momentum scan error for %s: %s", ticker, exc)

    # Sort by composite score descending
    signals.sort(key=lambda s: s.momentum_score, reverse=True)
    log.info(
        "Momentum scan: %d/%d tickers passed filters (top: %s)",
        len(signals), len(tickers),
        signals[0].ticker if signals else "none",
    )
    return signals


def _analyze_ticker_momentum(
    ticker: str,
    min_gain_pct: float,
    vol_multiplier: float,
) -> MomentumSignal | None:
    """Analyze a single ticker for momentum characteristics."""
    try:
        stock = yf.Ticker(ticker)

        # Get ~3 months of daily data for 52-week context + volume avg
        hist = stock.history(period="1y", auto_adjust=True)
        if hist.empty or len(hist) < 20:
            return None

        # Current day data
        latest = hist.iloc[-1]
        prev = hist.iloc[-2]

        current_price = float(latest["Close"])
        prev_close = float(prev["Close"])
        open_price = float(latest["Open"])
        current_volume = float(latest["Volume"])

        # Daily gain
        daily_gain_pct = ((current_price - prev_close) / prev_close) * 100

        # Gap up from previous close to today's open
        gap_up_pct = ((open_price - prev_close) / prev_close) * 100

        # Volume ratio (current vs 20-day average)
        avg_vol_20 = float(hist["Volume"].iloc[-21:-1].mean()) if len(hist) > 20 else float(hist["Volume"].mean())
        volume_ratio = current_volume / avg_vol_20 if avg_vol_20 > 0 else 0

        # 52-week high check
        high_52w = float(hist["High"].max())
        pct_from_high = ((high_52w - current_price) / high_52w) * 100
        is_52w_high = pct_from_high <= 2.0  # within 2% of 52-week high

        # Consecutive green days
        consecutive_green = 0
        for i in range(len(hist) - 1, 0, -1):
            if hist.iloc[i]["Close"] > hist.iloc[i - 1]["Close"]:
                consecutive_green += 1
            else:
                break

        # RSI-14 for context
        delta = hist["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi_series = 100 - (100 / (1 + rs))
        rsi_14 = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

        # ── Composite momentum score (0-100) ────────────────
        score = 0.0

        # Daily gain component (0-35 points)
        if daily_gain_pct >= min_gain_pct:
            score += min(35.0, 15.0 + (daily_gain_pct - min_gain_pct) * 4.0)
        elif daily_gain_pct >= 2.0:
            score += daily_gain_pct * 3.0  # partial credit

        # Volume component (0-30 points)
        if volume_ratio >= vol_multiplier:
            score += min(30.0, 15.0 + (volume_ratio - vol_multiplier) * 3.0)
        elif volume_ratio >= 1.5:
            score += volume_ratio * 5.0  # partial credit

        # 52-week high bonus (0-15 points)
        if is_52w_high:
            score += 15.0
        elif pct_from_high <= 5.0:
            score += 10.0
        elif pct_from_high <= 10.0:
            score += 5.0

        # Gap up bonus (0-10 points)
        if gap_up_pct >= 3.0:
            score += min(10.0, gap_up_pct * 2.0)

        # Consecutive green bonus (0-10 points)
        score += min(10.0, consecutive_green * 2.5)

        score = min(100.0, score)

        # Must pass at least one major filter to be included
        passes_gain = daily_gain_pct >= min_gain_pct
        passes_volume = volume_ratio >= vol_multiplier
        passes_52w = is_52w_high

        if not (passes_gain or passes_volume or passes_52w) and score < 40:
            return None

        return MomentumSignal(
            ticker=ticker,
            daily_gain_pct=round(daily_gain_pct, 2),
            volume_ratio=round(volume_ratio, 2),
            is_52w_high=is_52w_high,
            high_52w=round(high_52w, 2),
            current_price=round(current_price, 2),
            avg_volume=round(avg_vol_20, 0),
            current_volume=round(current_volume, 0),
            momentum_score=round(score, 1),
            gap_up_pct=round(gap_up_pct, 2),
            consecutive_green=consecutive_green,
            rsi_14=round(rsi_14, 1),
        )

    except Exception as exc:
        log.debug("Failed momentum analysis for %s: %s", ticker, exc)
        return None


def scan_52w_breakouts(tickers: list[str]) -> list[MomentumSignal]:
    """Convenience: return only tickers making new 52-week highs."""
    all_signals = scan_momentum(tickers, min_gain_pct=0.0, vol_multiplier=1.0)
    return [s for s in all_signals if s.is_52w_high]


def get_top_movers(
    tickers: list[str],
    top_n: int = 20,
    min_gain_pct: float = _DEFAULT_MIN_GAIN_PCT,
    vol_multiplier: float = _DEFAULT_VOL_MULT,
) -> list[MomentumSignal]:
    """Scan and return the top N momentum stocks by score."""
    signals = scan_momentum(tickers, min_gain_pct, vol_multiplier)
    return signals[:top_n]
