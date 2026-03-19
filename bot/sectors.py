"""Sector classification, rotation logic, and defensive switching.

Provides:
  • Ticker → sector mapping (GICS-like)
  • Sector performance tracking via SPY sector ETFs
  • Automatic switch to defensive sectors when cyclicals are weak
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yfinance as yf
import pandas as pd

log = logging.getLogger(__name__)

# ── Ticker → Sector mapping ────────────────────────────────
# Covers the default watchlist + common additions
SECTOR_MAP: dict[str, str] = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "ADBE": "Technology", "CRM": "Technology", "CSCO": "Technology",
    "INTC": "Technology", "ORCL": "Technology", "ACN": "Technology",
    "TXN": "Technology", "QCOM": "Technology", "AMD": "Technology",
    "AVGO": "Technology", "IBM": "Technology", "NFLX": "Technology",
    # Communication Services
    "GOOGL": "Communication Services", "META": "Communication Services",
    "DIS": "Communication Services",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "MCD": "Consumer Discretionary", "LOW": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary", "UBER": "Consumer Discretionary",
    # Consumer Staples (defensive)
    "WMT": "Consumer Staples", "PG": "Consumer Staples",
    "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "COST": "Consumer Staples",
    # Financials
    "JPM": "Financials", "V": "Financials", "MA": "Financials",
    "BAC": "Financials", "PYPL": "Financials",
    # Healthcare (defensive)
    "JNJ": "Healthcare", "UNH": "Healthcare", "PFE": "Healthcare",
    "ABT": "Healthcare", "TMO": "Healthcare", "MRK": "Healthcare",
    "LLY": "Healthcare", "MDT": "Healthcare", "AMGN": "Healthcare",
    # Energy
    "XOM": "Energy", "CVX": "Energy",
    # Industrials
    "GE": "Industrials", "CAT": "Industrials", "BA": "Industrials",
    # Utilities (defensive)
    # Real Estate (defensive)
}

# Sector ETFs for tracking performance
SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}

DEFENSIVE_SECTORS = {"Consumer Staples", "Healthcare", "Utilities"}
CYCLICAL_SECTORS = {"Technology", "Consumer Discretionary", "Financials", "Industrials"}


@dataclass
class SectorPerformance:
    sector: str
    etf: str
    return_1w: float  # 1-week return %
    return_1m: float  # 1-month return %
    is_weak: bool  # underperforming significantly


def get_sector(ticker: str) -> str:
    """Return the sector for a ticker, or 'Unknown'."""
    return SECTOR_MAP.get(ticker, "Unknown")


def get_sector_performance(period_short: str = "5d", period_long: str = "1mo") -> list[SectorPerformance]:
    """Fetch recent performance for each sector via ETFs."""
    results: list[SectorPerformance] = []
    for sector, etf in SECTOR_ETFS.items():
        try:
            df_short = yf.download(etf, period=period_short, progress=False)
            df_long = yf.download(etf, period=period_long, progress=False)
            if df_short.empty or df_long.empty or len(df_short) < 2 or len(df_long) < 2:
                continue

            close_short = df_short["Close"].squeeze()
            close_long = df_long["Close"].squeeze()

            ret_1w = ((close_short.iloc[-1] - close_short.iloc[0]) / close_short.iloc[0]) * 100
            ret_1m = ((close_long.iloc[-1] - close_long.iloc[0]) / close_long.iloc[0]) * 100

            # A sector is "weak" if it dropped >3% in the last week
            is_weak = float(ret_1w) < -3.0

            results.append(SectorPerformance(
                sector=sector, etf=etf,
                return_1w=float(ret_1w), return_1m=float(ret_1m),
                is_weak=is_weak,
            ))
        except Exception as exc:
            log.warning("Failed to fetch sector data for %s (%s): %s", sector, etf, exc)

    log.info("Sector performance: %s", {r.sector: f"{r.return_1w:+.1f}%/wk" for r in results})
    return results


def should_rotate_to_defensive(sector_perfs: list[SectorPerformance]) -> bool:
    """Return True if most cyclical sectors are weak → switch to defensives."""
    weak_cyclicals = sum(1 for p in sector_perfs if p.sector in CYCLICAL_SECTORS and p.is_weak)
    total_cyclicals = sum(1 for p in sector_perfs if p.sector in CYCLICAL_SECTORS)
    if total_cyclicals == 0:
        return False
    ratio = weak_cyclicals / total_cyclicals
    rotate = ratio >= 0.5  # If ≥50% of cyclicals are weak
    if rotate:
        log.info("⚠️ Sector rotation triggered: %d/%d cyclical sectors weak", weak_cyclicals, total_cyclicals)
    return rotate


def filter_watchlist_by_rotation(
    watchlist: list[str],
    sector_perfs: list[SectorPerformance],
    defensive_mode: bool,
) -> list[str]:
    """Filter the watchlist based on sector rotation.

    In defensive mode: prioritise defensive stocks.
    In normal mode: return all stocks.
    """
    if not defensive_mode:
        return watchlist

    defensive_tickers = [t for t in watchlist if get_sector(t) in DEFENSIVE_SECTORS]
    other_tickers = [t for t in watchlist if get_sector(t) not in DEFENSIVE_SECTORS]

    # In defensive mode, put defensive stocks first but still include some others
    # so we don't miss great opportunities
    log.info("Defensive mode: prioritising %d defensive stocks", len(defensive_tickers))
    return defensive_tickers + other_tickers[:10]


def get_sector_allocation(open_buys: list[dict]) -> dict[str, float]:
    """Return sector → total EUR value mapping for open positions."""
    alloc: dict[str, float] = {}
    for buy in open_buys:
        sector = get_sector(buy["ticker"])
        alloc[sector] = alloc.get(sector, 0) + buy.get("value", 0)
    return alloc


def sector_allocation_pct(open_buys: list[dict]) -> dict[str, float]:
    """Return sector → % of total portfolio."""
    alloc = get_sector_allocation(open_buys)
    total = sum(alloc.values())
    if total == 0:
        return {}
    return {s: (v / total) * 100 for s, v in alloc.items()}
