"""Performance analytics – Sharpe ratio, drawdown, sector stats.

All calculations are based on the trades stored in the SQLite database.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

from bot.database import TradeDB
from bot.sectors import get_sector

log = logging.getLogger(__name__)


@dataclass
class AnalyticsReport:
    # Overall
    total_trades: int
    total_pnl: float
    win_rate: float  # 0-100 %
    avg_win: float
    avg_loss: float
    profit_factor: float  # gross_wins / gross_losses

    # Risk
    sharpe_ratio: float | None
    max_drawdown_pct: float
    max_drawdown_eur: float

    # By sector
    sector_stats: dict[str, SectorStat]

    # Best / worst
    best_trade: dict | None
    worst_trade: dict | None

    # AI confidence analysis
    confidence_buckets: dict[str, ConfidenceBucket]


@dataclass
class SectorStat:
    sector: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


@dataclass
class ConfidenceBucket:
    """Stats for trades grouped by AI confidence score ranges."""
    bucket_label: str  # e.g. "75-85", "85-95", "95-100"
    trades: int
    wins: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


def compute_analytics(db: TradeDB) -> AnalyticsReport:
    """Compute full analytics from the trade database."""
    all_trades = db.get_all_trades(limit=10000)
    sells = [t for t in all_trades if t["side"] == "SELL"]
    buys = [t for t in all_trades if t["side"] == "BUY"]

    total_trades = len(all_trades)
    pnl_values = [t.get("pnl", 0) or 0 for t in sells]
    total_pnl = sum(pnl_values)

    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p <= 0]

    win_rate = (len(wins) / len(sells) * 100) if sells else 0
    avg_win = (sum(wins) / len(wins)) if wins else 0
    avg_loss = (sum(losses) / len(losses)) if losses else 0

    gross_wins = sum(wins) if wins else 0
    gross_losses = abs(sum(losses)) if losses else 0
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf") if gross_wins > 0 else 0

    # ── Sharpe Ratio ────────────────────────────────────────
    sharpe = _calc_sharpe(pnl_values)

    # ── Max Drawdown ────────────────────────────────────────
    dd_pct, dd_eur = _calc_max_drawdown(sells)

    # ── Sector stats ────────────────────────────────────────
    sector_stats = _calc_sector_stats(sells)

    # ── Best / worst trades ─────────────────────────────────
    best_trade = max(sells, key=lambda t: t.get("pnl", 0) or 0) if sells else None
    worst_trade = min(sells, key=lambda t: t.get("pnl", 0) or 0) if sells else None

    # ── Confidence buckets ──────────────────────────────────
    confidence_buckets = _calc_confidence_buckets(buys, sells)

    return AnalyticsReport(
        total_trades=total_trades,
        total_pnl=total_pnl,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        sharpe_ratio=sharpe,
        max_drawdown_pct=dd_pct,
        max_drawdown_eur=dd_eur,
        sector_stats=sector_stats,
        best_trade=best_trade,
        worst_trade=worst_trade,
        confidence_buckets=confidence_buckets,
    )


def _calc_sharpe(pnl_values: list[float], risk_free_rate: float = 0.0) -> float | None:
    """Annualised Sharpe ratio from per-trade P&L values.

    Assumes ~252 trading days/year and roughly 1 trade/day average.
    """
    if len(pnl_values) < 2:
        return None

    mean_pnl = sum(pnl_values) / len(pnl_values)
    variance = sum((p - mean_pnl) ** 2 for p in pnl_values) / (len(pnl_values) - 1)
    std_pnl = math.sqrt(variance) if variance > 0 else 0

    if std_pnl == 0:
        return None

    # Annualise: assume ~1 trade per day on average
    trades_per_year = min(len(pnl_values), 252)
    annualised_return = mean_pnl * trades_per_year
    annualised_std = std_pnl * math.sqrt(trades_per_year)

    sharpe = (annualised_return - risk_free_rate) / annualised_std
    return round(sharpe, 3)


def _calc_max_drawdown(sells: list[dict]) -> tuple[float, float]:
    """Calculate maximum drawdown from cumulative P&L of sell trades."""
    if not sells:
        return 0.0, 0.0

    # Sort by timestamp
    sorted_sells = sorted(sells, key=lambda t: t.get("timestamp", ""))
    cumulative = 0.0
    peak = 0.0
    max_dd_eur = 0.0

    for t in sorted_sells:
        pnl = t.get("pnl", 0) or 0
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_dd_eur:
            max_dd_eur = drawdown

    # As percentage of peak
    max_dd_pct = (max_dd_eur / peak * 100) if peak > 0 else 0
    return round(max_dd_pct, 2), round(max_dd_eur, 2)


def _calc_sector_stats(sells: list[dict]) -> dict[str, SectorStat]:
    """Win rate and P&L broken down by sector."""
    buckets: dict[str, list[dict]] = {}
    for t in sells:
        sector = get_sector(t.get("ticker", ""))
        buckets.setdefault(sector, []).append(t)

    stats: dict[str, SectorStat] = {}
    for sector, trades in buckets.items():
        pnl_list = [t.get("pnl", 0) or 0 for t in trades]
        w = sum(1 for p in pnl_list if p > 0)
        l = sum(1 for p in pnl_list if p <= 0)
        total = sum(pnl_list)
        stats[sector] = SectorStat(
            sector=sector,
            trades=len(trades),
            wins=w,
            losses=l,
            win_rate=(w / len(trades) * 100) if trades else 0,
            total_pnl=total,
            avg_pnl=(total / len(trades)) if trades else 0,
        )

    return stats


def _calc_confidence_buckets(buys: list[dict], sells: list[dict]) -> dict[str, ConfidenceBucket]:
    """Analyse performance by AI confidence score ranges.

    Maps sell trades back to their buy's confidence score and groups them.
    """
    # Build a ticker → most recent buy confidence map
    buy_confidence: dict[str, float] = {}
    for b in sorted(buys, key=lambda t: t.get("timestamp", "")):
        score = b.get("confidence", b.get("sentiment_score", 0)) or 0
        buy_confidence[b["ticker"]] = score

    # Define buckets
    bucket_ranges = [
        ("0-50", 0, 50),
        ("50-75", 50, 75),
        ("75-85", 75, 85),
        ("85-95", 85, 95),
        ("95-100", 95, 101),
    ]

    results: dict[str, ConfidenceBucket] = {}
    for label, lo, hi in bucket_ranges:
        # Find sells whose buy confidence falls in this range
        matching = []
        for s in sells:
            conf = buy_confidence.get(s["ticker"], 0)
            # Confidence could be stored as 0-100 or 0-1; normalise to 0-100
            if conf <= 1.0:
                conf *= 100
            if lo <= conf < hi:
                matching.append(s)

        pnl_list = [t.get("pnl", 0) or 0 for t in matching]
        w = sum(1 for p in pnl_list if p > 0)
        total_pnl = sum(pnl_list)
        results[label] = ConfidenceBucket(
            bucket_label=label,
            trades=len(matching),
            wins=w,
            win_rate=(w / len(matching) * 100) if matching else 0,
            avg_pnl=(total_pnl / len(matching)) if matching else 0,
            total_pnl=total_pnl,
        )

    return results
