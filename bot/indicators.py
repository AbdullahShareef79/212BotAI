"""Technical indicators on daily OHLCV candles.

Uses *yfinance* for price history and *ta* library for indicator math.
Returns a clean dataclass with the latest values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import BollingerBands

log = logging.getLogger(__name__)


@dataclass
class Indicators:
    ticker: str
    close: float
    rsi: float
    macd_line: float
    macd_signal: float
    macd_hist: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_pband: float  # 0 = lower band, 1 = upper band
    atr_14: float | None = None  # optional, handy for stop-loss sizing

    @property
    def at_lower_bb(self) -> bool:
        """Price is within 1 % of the lower Bollinger Band."""
        if self.bb_lower == 0:
            return False
        return self.close <= self.bb_lower * 1.01

    @property
    def rsi_oversold(self) -> bool:
        return self.rsi < 45

    @property
    def macd_bullish(self) -> bool:
        return self.macd_hist > 0


def fetch_indicators(ticker: str, period: str = "6mo", interval: str = "1d") -> Indicators | None:
    """Download daily candles for *ticker* and compute RSI / MACD / Bollinger Bands.

    Returns ``None`` when there is not enough data.
    """
    try:
        df: pd.DataFrame = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty or len(df) < 30:
            log.warning("Insufficient price data for %s (%d rows)", ticker, len(df))
            return None

        close: pd.Series = df["Close"].squeeze()

        # ── RSI (14) ────────────────────────────────────────
        rsi_ind = RSIIndicator(close=close, window=14)
        rsi_series = rsi_ind.rsi()

        # ── MACD (12, 26, 9) ────────────────────────────────
        macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        macd_line = macd_ind.macd()
        macd_signal = macd_ind.macd_signal()
        macd_hist = macd_ind.macd_diff()

        # ── Bollinger Bands (20, 2) ─────────────────────────
        bb_ind = BollingerBands(close=close, window=20, window_dev=2)
        bb_upper = bb_ind.bollinger_hband()
        bb_middle = bb_ind.bollinger_mavg()
        bb_lower = bb_ind.bollinger_lband()
        bb_pband = bb_ind.bollinger_pband()

        # ── ATR (14) ────────────────────────────────────────
        high = df["High"].squeeze()
        low = df["Low"].squeeze()
        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr_14 = tr.rolling(14).mean()

        # Latest values
        latest = -1
        result = Indicators(
            ticker=ticker,
            close=float(close.iloc[latest]),
            rsi=float(rsi_series.iloc[latest]) if not np.isnan(rsi_series.iloc[latest]) else 50.0,
            macd_line=float(macd_line.iloc[latest]) if not np.isnan(macd_line.iloc[latest]) else 0.0,
            macd_signal=float(macd_signal.iloc[latest]) if not np.isnan(macd_signal.iloc[latest]) else 0.0,
            macd_hist=float(macd_hist.iloc[latest]) if not np.isnan(macd_hist.iloc[latest]) else 0.0,
            bb_upper=float(bb_upper.iloc[latest]) if not np.isnan(bb_upper.iloc[latest]) else 0.0,
            bb_middle=float(bb_middle.iloc[latest]) if not np.isnan(bb_middle.iloc[latest]) else 0.0,
            bb_lower=float(bb_lower.iloc[latest]) if not np.isnan(bb_lower.iloc[latest]) else 0.0,
            bb_pband=float(bb_pband.iloc[latest]) if not np.isnan(bb_pband.iloc[latest]) else 0.5,
            atr_14=float(atr_14.iloc[latest]) if not np.isnan(atr_14.iloc[latest]) else None,
        )
        log.info(
            "%s  close=%.2f  RSI=%.1f  MACD_H=%.3f  BB_P=%.2f",
            ticker, result.close, result.rsi, result.macd_hist, result.bb_pband,
        )
        return result
    except Exception as exc:
        log.error("Indicator error for %s: %s", ticker, exc)
        return None
