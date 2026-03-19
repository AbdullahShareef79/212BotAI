"""Relative strength vs S&P 500.

Only buy stocks that are outperforming the broader market over the
recent period – ensures we ride momentum, not catch falling knives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yfinance as yf
import pandas as pd

log = logging.getLogger(__name__)

_SPX_TICKER = "^GSPC"  # S&P 500 index


@dataclass
class RelativeStrength:
    ticker: str
    stock_return: float  # % return over period
    sp500_return: float  # % return over period
    rs_ratio: float  # stock_return / sp500_return (>1 = outperforming)
    beating_market: bool  # stock_return > sp500_return

    @property
    def rs_display(self) -> str:
        return f"{self.rs_ratio:.2f}" if self.sp500_return != 0 else "N/A"


def _calc_return(ticker: str, period: str = "1mo") -> float | None:
    """Compute the total return for *ticker* over *period*."""
    try:
        df = yf.download(ticker, period=period, progress=False)
        if df.empty or len(df) < 2:
            return None
        close = df["Close"].squeeze()
        return float(((close.iloc[-1] - close.iloc[0]) / close.iloc[0]) * 100)
    except Exception as exc:
        log.debug("Return calc failed for %s: %s", ticker, exc)
        return None


# Cache SPX return for the session to avoid redundant downloads
_spx_cache: dict[str, float] = {}


def get_sp500_return(period: str = "1mo") -> float:
    """Get S&P 500 return over *period*, with caching."""
    if period in _spx_cache:
        return _spx_cache[period]
    ret = _calc_return(_SPX_TICKER, period)
    if ret is None:
        log.warning("Could not fetch S&P 500 return – defaulting to 0")
        ret = 0.0
    _spx_cache[period] = ret
    return ret


def clear_cache() -> None:
    """Clear the cached S&P 500 returns (call at start of each scan)."""
    _spx_cache.clear()


def calc_relative_strength(ticker: str, period: str = "1mo") -> RelativeStrength | None:
    """Calculate relative strength of *ticker* vs S&P 500."""
    stock_ret = _calc_return(ticker, period)
    if stock_ret is None:
        return None

    spx_ret = get_sp500_return(period)
    rs_ratio = (stock_ret / spx_ret) if spx_ret != 0 else (1.0 if stock_ret >= 0 else -1.0)

    result = RelativeStrength(
        ticker=ticker,
        stock_return=stock_ret,
        sp500_return=spx_ret,
        rs_ratio=rs_ratio,
        beating_market=stock_ret > spx_ret,
    )
    log.debug(
        "RS %s: stock=%.2f%%, SPX=%.2f%%, ratio=%.2f, beating=%s",
        ticker, stock_ret, spx_ret, rs_ratio, result.beating_market,
    )
    return result


def is_beating_market(ticker: str, period: str = "1mo") -> bool:
    """Quick check: is *ticker* outperforming S&P 500?"""
    rs = calc_relative_strength(ticker, period)
    if rs is None:
        return True  # No data → don't filter out
    return rs.beating_market
