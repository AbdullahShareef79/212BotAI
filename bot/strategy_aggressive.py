"""Aggressive momentum strategy v3 – catalyst-driven entries with conviction sizing.

This runs ALONGSIDE the safe strategy (not replacing it).

BUY  when: momentum score >= 70  AND  catalyst score >= 7
           AND  volume ratio >= 3x  AND  within portfolio allocation limits
EXIT when: Multi-tier take profit (+15% / +30% / +50%)
           OR  trailing stop from each tier
           OR  hard stop loss at -5%
           OR  catalyst invalidated (AI re-check)

Position sizing by conviction:
  catalyst 7  → 5% of portfolio  (moderate conviction)
  catalyst 8  → 15% of portfolio (high conviction)
  catalyst 9+ → 25% of portfolio (maximum conviction)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from config import cfg
from bot.database import TradeDB, TradeRecord
from bot.indicators import Indicators, fetch_indicators
from bot.news import NewsClient
from bot.sentiment import SentimentAnalyzer, AnalysisResult
from bot.catalyst import CatalystAnalyzer, CatalystResult
from bot.momentum import MomentumSignal, scan_momentum, get_top_movers
from bot.trading212 import Trading212Client
from bot.notifier import TelegramNotifier
from bot.watchlist import get_tier, get_aggressive_watchlist
from bot.sectors import get_sector, sector_allocation_pct

log = logging.getLogger(__name__)


@dataclass
class AggressiveScanResult:
    """Result from the aggressive strategy scanner."""
    ticker: str
    action: str  # BUY / SELL / HOLD
    reason: str
    strategy_type: str = "AGGRESSIVE"
    momentum: MomentumSignal | None = None
    catalyst: CatalystResult | None = None
    sentiment: AnalysisResult | None = None
    indicators: Indicators | None = None
    entry_price: float | None = None
    pnl_pct: float | None = None
    confidence: int = 0
    catalyst_score: int = 0
    sector: str = ""
    tier: str = "AGGRESSIVE"
    conviction_tier: str = "NONE"  # NONE / MODERATE / HIGH / MAX
    position_size_pct: float = 0.0
    sell_type: str = ""
    tp_tier_hit: int = 0  # 0=none, 1=+15%, 2=+30%, 3=+50%


class AggressiveStrategy:
    """Momentum + catalyst driven aggressive trading strategy."""

    def __init__(
        self,
        t212: Trading212Client,
        news: NewsClient,
        analyzer: SentimentAnalyzer,
        catalyst_analyzer: CatalystAnalyzer,
        db: TradeDB,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.t212 = t212
        self.news = news
        self.analyzer = analyzer
        self.catalyst = catalyst_analyzer
        self.db = db
        self.notifier = notifier

    # ── public ───────────────────────────────────────────────

    def run_aggressive_scan(
        self,
        watchlist: list[str] | None = None,
        max_momentum_analyze: int = 20,
    ) -> list[AggressiveScanResult]:
        """Run the aggressive momentum scan.

        1. Scan entire Tier 2 watchlist for momentum signals
        2. Take top N by momentum score
        3. Run catalyst analysis on those
        4. Enter positions where catalyst >= 7
        5. Check existing aggressive positions for exits
        """
        tickers = watchlist or get_aggressive_watchlist()
        results: list[AggressiveScanResult] = []

        log.info("=== Starting AGGRESSIVE scan on %d tickers ===", len(tickers))

        # 1. Check existing aggressive positions for sell signals
        sell_results = self._check_aggressive_sells()
        results.extend(sell_results)

        # 2. Scan for momentum
        momentum_signals = get_top_movers(
            tickers,
            top_n=max_momentum_analyze,
            min_gain_pct=cfg.momentum_min_gain_pct,
            vol_multiplier=cfg.momentum_vol_multiplier,
        )

        if not momentum_signals:
            log.info("No momentum signals found in Tier 2 watchlist")
            return results

        log.info(
            "Found %d momentum signals, analyzing top %d for catalysts",
            len(momentum_signals), min(len(momentum_signals), max_momentum_analyze),
        )

        # 3. Run catalyst analysis on momentum stocks
        buy_results = self._check_aggressive_buys(momentum_signals)
        results.extend(buy_results)

        log.info(
            "=== Aggressive scan complete: %d sells, %d buys, %d holds ===",
            sum(1 for r in results if r.action == "SELL"),
            sum(1 for r in results if r.action == "BUY"),
            sum(1 for r in results if r.action == "HOLD"),
        )
        return results

    # ── sell check (multi-tier TP + trailing) ────────────────

    def _check_aggressive_sells(self) -> list[AggressiveScanResult]:
        results: list[AggressiveScanResult] = []
        open_buys = self.db.get_open_buys()

        for buy in open_buys:
            # Only process aggressive strategy positions
            if buy.get("strategy_type") != "AGGRESSIVE":
                continue

            ticker = buy["ticker"]
            sector = buy.get("sector") or get_sector(ticker)
            ind = fetch_indicators(ticker)
            if not ind:
                continue

            entry_price = buy["price"]
            current_price = ind.close
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            buy_id = buy["id"]
            tp_tier = buy.get("tp_tier", 0) or 0

            # ── Trailing stop from last TP tier ─────────────
            trailing_high = buy.get("trailing_stop_high") or entry_price
            if current_price > trailing_high:
                trailing_high = current_price
                self.db.update_trailing_stop(buy_id, trailing_high)

            # Dynamic trailing based on TP tier reached
            trailing_pct = self._get_trailing_pct(tp_tier)
            trailing_drop = ((trailing_high - current_price) / trailing_high) * 100

            if trailing_drop >= trailing_pct and pnl_pct > 0:
                reason = (
                    f"AGG trailing stop: dropped {trailing_drop:.1f}% from "
                    f"high {trailing_high:.2f} (tier {tp_tier} trail={trailing_pct}%)"
                )
                self._execute_aggressive_sell(
                    ticker, ind, buy, pnl_pct, reason, "AGG_TRAILING_SL",
                )
                results.append(AggressiveScanResult(
                    ticker, "SELL", reason, indicators=ind,
                    entry_price=entry_price, pnl_pct=pnl_pct,
                    sector=sector, sell_type="AGG_TRAILING_SL",
                ))
                continue

            # ── Hard stop loss (-5%) ────────────────────────
            if pnl_pct <= -cfg.agg_stop_loss_pct:
                reason = f"AGG stop-loss: {pnl_pct:+.2f}% (limit -{cfg.agg_stop_loss_pct}%)"
                self._execute_aggressive_sell(
                    ticker, ind, buy, pnl_pct, reason, "AGG_SL",
                )
                results.append(AggressiveScanResult(
                    ticker, "SELL", reason, indicators=ind,
                    entry_price=entry_price, pnl_pct=pnl_pct,
                    sector=sector, sell_type="AGG_SL",
                ))
                continue

            # ── Multi-tier take profit ──────────────────────
            # Tier 1: +15% → sell 33%, tighten trailing stop
            if pnl_pct >= cfg.agg_tp_tier1 and tp_tier < 1:
                reason = f"AGG TP Tier 1: +{pnl_pct:.1f}% (selling 33%)"
                self._execute_aggressive_partial(
                    ticker, ind, buy, pnl_pct, reason, tp_tier=1, sell_fraction=0.33,
                )
                results.append(AggressiveScanResult(
                    ticker, "SELL", reason, indicators=ind,
                    entry_price=entry_price, pnl_pct=pnl_pct,
                    sector=sector, sell_type="AGG_TP1", tp_tier_hit=1,
                ))
                continue

            # Tier 2: +30% → sell another 33%, tighten more
            if pnl_pct >= cfg.agg_tp_tier2 and tp_tier == 1:
                reason = f"AGG TP Tier 2: +{pnl_pct:.1f}% (selling 33%)"
                self._execute_aggressive_partial(
                    ticker, ind, buy, pnl_pct, reason, tp_tier=2, sell_fraction=0.50,
                )
                results.append(AggressiveScanResult(
                    ticker, "SELL", reason, indicators=ind,
                    entry_price=entry_price, pnl_pct=pnl_pct,
                    sector=sector, sell_type="AGG_TP2", tp_tier_hit=2,
                ))
                continue

            # Tier 3: +50% → sell remaining (full exit)
            if pnl_pct >= cfg.agg_tp_tier3 and tp_tier == 2:
                reason = f"AGG TP Tier 3: +{pnl_pct:.1f}% (full exit!)"
                self._execute_aggressive_sell(
                    ticker, ind, buy, pnl_pct, reason, "AGG_TP3",
                )
                results.append(AggressiveScanResult(
                    ticker, "SELL", reason, indicators=ind,
                    entry_price=entry_price, pnl_pct=pnl_pct,
                    sector=sector, sell_type="AGG_TP3", tp_tier_hit=3,
                ))
                continue

            # ── Hold ────────────────────────────────────────
            results.append(AggressiveScanResult(
                ticker, "HOLD",
                f"AGG P&L {pnl_pct:+.2f}%, tier {tp_tier}, trail {trailing_high:.2f}",
                indicators=ind, entry_price=entry_price, pnl_pct=pnl_pct,
                sector=sector,
            ))

        return results

    def _get_trailing_pct(self, tp_tier: int) -> float:
        """Dynamic trailing stop % based on which TP tier has been reached."""
        if tp_tier >= 2:
            return 5.0   # After +30%, trail at 5% from high
        elif tp_tier >= 1:
            return 8.0   # After +15%, trail at 8% from high
        return cfg.agg_stop_loss_pct  # Before any TP, use hard SL

    # ── buy check (momentum + catalyst gated) ────────────────

    def _check_aggressive_buys(
        self, momentum_signals: list[MomentumSignal],
    ) -> list[AggressiveScanResult]:
        results: list[AggressiveScanResult] = []
        open_count = self.db.count_open_positions()
        alloc_pct = sector_allocation_pct(self.db.get_open_buys())

        for signal in momentum_signals:
            ticker = signal.ticker
            sector = get_sector(ticker)

            # Portfolio limit check
            if open_count >= cfg.max_positions + cfg.agg_max_positions:
                results.append(AggressiveScanResult(
                    ticker, "HOLD", "Max total positions reached",
                    momentum=signal, sector=sector,
                ))
                break

            # Skip if already holding
            if self.db.get_open_buy_for_ticker(ticker):
                continue

            # ── Sector allocation cap (shared with safe strategy) ──
            if alloc_pct.get(sector, 0) >= cfg.max_sector_pct:
                results.append(AggressiveScanResult(
                    ticker, "HOLD",
                    f"Sector cap: {sector} at {alloc_pct.get(sector, 0):.0f}% (max {cfg.max_sector_pct}%)",
                    momentum=signal, sector=sector,
                ))
                continue

            # Momentum score threshold
            if signal.momentum_score < 50:
                results.append(AggressiveScanResult(
                    ticker, "HOLD",
                    f"Momentum score too low: {signal.momentum_score:.0f} (need >=50)",
                    momentum=signal, sector=sector,
                ))
                continue

            # ── Catalyst analysis ────────────────────────────
            headlines = self.news.fetch_headlines(ticker, days_back=3, max_articles=12)

            price_context = (
                f"Up {signal.daily_gain_pct:+.1f}% today, "
                f"{signal.volume_ratio:.1f}x avg volume, "
                f"{'at 52w high' if signal.is_52w_high else f'{((signal.high_52w - signal.current_price) / signal.high_52w * 100):.0f}% from 52w high'}, "
                f"{signal.consecutive_green} consecutive green days, "
                f"RSI={signal.rsi_14:.0f}"
            )

            cat = self.catalyst.analyze(ticker, headlines, price_context)

            # Must have catalyst score >= min threshold
            if cat.catalyst_score < cfg.min_catalyst_score:
                results.append(AggressiveScanResult(
                    ticker, "HOLD",
                    f"Catalyst too weak: {cat.catalyst_score}/10 (need >={cfg.min_catalyst_score}) "
                    f"[{cat.catalyst_type}] {cat.reasoning[:60]}",
                    momentum=signal, catalyst=cat, sector=sector,
                    catalyst_score=cat.catalyst_score,
                ))
                continue

            # ── Also run standard sentiment check ────────────
            sent = self.analyzer.analyze(ticker, headlines)
            if sent.is_negative:
                results.append(AggressiveScanResult(
                    ticker, "HOLD",
                    f"AI sentiment NEGATIVE despite catalyst (conf={sent.confidence}%)",
                    momentum=signal, catalyst=cat, sentiment=sent, sector=sector,
                    catalyst_score=cat.catalyst_score,
                ))
                continue

            # ── Calculate conviction-based position size ─────
            conviction_tier = cat.conviction_tier
            position_size_pct = self._calc_position_size(conviction_tier)

            ind = fetch_indicators(ticker)

            reason = (
                f"AGG BUY: momentum={signal.momentum_score:.0f} "
                f"catalyst={cat.catalyst_score}/10 [{cat.catalyst_type}] "
                f"vol={signal.volume_ratio:.1f}x "
                f"conviction={conviction_tier} size={position_size_pct:.0f}% "
                f"| {cat.reasoning[:80]}"
            )

            self._execute_aggressive_buy(
                ticker, ind, sent, cat, signal, reason, sector,
                conviction_tier, position_size_pct,
            )
            open_count += 1
            # Refresh sector allocation after buy
            alloc_pct = sector_allocation_pct(self.db.get_open_buys())
            results.append(AggressiveScanResult(
                ticker, "BUY", reason,
                momentum=signal, catalyst=cat, sentiment=sent,
                indicators=ind, catalyst_score=cat.catalyst_score,
                sector=sector, conviction_tier=conviction_tier,
                position_size_pct=position_size_pct,
                confidence=sent.confidence,
            ))

        return results

    def _calc_position_size(self, conviction_tier: str) -> float:
        """Return position size as % of portfolio based on conviction."""
        sizes = {
            "MAX": cfg.agg_size_max_pct,        # 25%
            "HIGH": cfg.agg_size_high_pct,       # 15%
            "MODERATE": cfg.agg_size_moderate_pct,  # 5%
        }
        return sizes.get(conviction_tier, cfg.agg_size_moderate_pct)

    # ── execution ────────────────────────────────────────────

    def _execute_aggressive_buy(
        self,
        ticker: str,
        ind: Indicators | None,
        sent: AnalysisResult,
        cat: CatalystResult,
        signal: MomentumSignal,
        reason: str,
        sector: str,
        conviction_tier: str,
        position_size_pct: float,
    ) -> None:
        price = signal.current_price
        # Conviction-based sizing (% of order_size_eur * multiplier)
        base_value = cfg.order_size_eur
        size_multiplier = position_size_pct / 5.0  # 5% = 1x, 15% = 3x, 25% = 5x
        value = base_value * size_multiplier
        quantity = value / price if price > 0 else 0

        if not cfg.dry_run:
            try:
                self.t212.place_value_order(ticker, value)
                log.info("AGG LIVE BUY %s EUR%.2f (conviction=%s)", ticker, value, conviction_tier)
            except Exception as exc:
                log.error("AGG order failed for %s: %s", ticker, exc)
                if self.notifier:
                    self.notifier.send(f"❌ AGG BUY FAILED {ticker}: {exc}")
                return
        else:
            log.info(
                "AGG DRY-RUN BUY %s EUR%.2f (%.4f @ %.2f) conviction=%s",
                ticker, value, quantity, price, conviction_tier,
            )

        record = TradeRecord(
            id=None, ticker=ticker, side="BUY",
            quantity=quantity, price=price, value=value,
            sentiment=sent.sentiment, sentiment_score=sent.sentiment_score,
            rsi=ind.rsi if ind else 0, bb_pband=ind.bb_pband if ind else 0,
            reason=reason, dry_run=cfg.dry_run,
            timestamp=datetime.utcnow().isoformat(),
            sector=sector, confidence=sent.confidence,
            price_target=sent.price_target,
            price_target_timeframe=sent.price_target_timeframe or "",
            trailing_stop_high=price,
            # v3 fields
            strategy_type="AGGRESSIVE",
            catalyst_score=cat.catalyst_score,
            catalyst_type=cat.catalyst_type,
            position_size_pct=position_size_pct,
            tp_tier=0,
        )
        self.db.insert_trade(record)

        msg = (
            f"{'🧪 DRY' if cfg.dry_run else '🚀 LIVE'} AGG BUY {ticker} ({sector})\n"
            f"€{value:.2f} ({quantity:.4f} shares @ €{price:.2f})\n"
            f"Momentum: {signal.momentum_score:.0f}/100 | "
            f"Catalyst: {cat.catalyst_score}/10 [{cat.catalyst_type}]\n"
            f"Conviction: {conviction_tier} ({position_size_pct:.0f}% size)\n"
            f"Vol: {signal.volume_ratio:.1f}x | Day: {signal.daily_gain_pct:+.1f}%\n"
            f"{cat.reasoning[:200]}"
        )
        if self.notifier:
            self.notifier.send(msg)

    def _execute_aggressive_partial(
        self,
        ticker: str,
        ind: Indicators,
        buy: dict,
        pnl_pct: float,
        reason: str,
        tp_tier: int,
        sell_fraction: float,
    ) -> None:
        """Sell a fraction at a TP tier, update the position."""
        price = ind.close
        full_qty = buy["quantity"]
        sell_qty = full_qty * sell_fraction
        sell_value = sell_qty * price
        buy_portion_value = buy["value"] * sell_fraction
        pnl = sell_value - buy_portion_value

        if not cfg.dry_run:
            try:
                self.t212.place_market_order(ticker, -abs(sell_qty))
                log.info("AGG LIVE PARTIAL SELL %s qty=%.4f (tier %d)", ticker, sell_qty, tp_tier)
            except Exception as exc:
                log.error("AGG partial sell failed %s: %s", ticker, exc)
                if self.notifier:
                    self.notifier.send(f"❌ AGG PARTIAL SELL FAILED {ticker}: {exc}")
                return
        else:
            log.info(
                "AGG DRY-RUN PARTIAL SELL %s qty=%.4f P&L=EUR%.2f (tier %d)",
                ticker, sell_qty, pnl, tp_tier,
            )

        # Update TP tier on the original buy
        self.db.update_tp_tier(buy["id"], tp_tier)
        if tp_tier == 1:
            self.db.mark_partial_sold(buy["id"])

        record = TradeRecord(
            id=None, ticker=ticker, side="PARTIAL_SELL",
            quantity=sell_qty, price=price, value=sell_value,
            sentiment="", sentiment_score=0,
            rsi=ind.rsi, bb_pband=ind.bb_pband,
            reason=reason, dry_run=cfg.dry_run,
            timestamp=datetime.utcnow().isoformat(),
            pnl=pnl, sector=buy.get("sector", ""),
            sell_type=f"AGG_TP{tp_tier}",
            strategy_type="AGGRESSIVE",
            tp_tier=tp_tier,
        )
        self.db.insert_trade(record)

        msg = (
            f"{'🧪 DRY' if cfg.dry_run else '🚀 LIVE'} AGG TP{tp_tier} {ticker}\n"
            f"Sold {sell_fraction*100:.0f}% ({sell_qty:.4f}) @ €{price:.2f}\n"
            f"P&L: €{pnl:+.2f} ({pnl_pct:+.2f}%)\n"
            f"Remaining rides to TP{tp_tier + 1}"
        )
        if self.notifier:
            self.notifier.send(msg)

    def _execute_aggressive_sell(
        self,
        ticker: str,
        ind: Indicators,
        buy: dict,
        pnl_pct: float,
        reason: str,
        sell_type: str,
    ) -> None:
        """Full exit of an aggressive position."""
        price = ind.close
        quantity = buy["quantity"]
        # Adjust for any partial sells
        tp_tier = buy.get("tp_tier", 0) or 0
        if tp_tier >= 2:
            quantity *= 0.34  # After selling 33% + 33%, ~34% remains
        elif tp_tier >= 1:
            quantity *= 0.67  # After selling 33%, 67% remains

        value = quantity * price
        # Calculate remaining cost basis
        if tp_tier >= 2:
            buy_value = buy["value"] * 0.34
        elif tp_tier >= 1:
            buy_value = buy["value"] * 0.67
        else:
            buy_value = buy["value"]
        pnl = value - buy_value

        if not cfg.dry_run:
            try:
                pos = self.t212.get_position(ticker)
                if pos:
                    sell_qty = pos.get("quantity", quantity)
                    self.t212.place_market_order(ticker, -abs(sell_qty))
                    log.info("AGG LIVE SELL %s qty=%.4f", ticker, sell_qty)
                else:
                    log.warning("No T212 position for %s", ticker)
            except Exception as exc:
                log.error("AGG sell failed %s: %s", ticker, exc)
                if self.notifier:
                    self.notifier.send(f"❌ AGG SELL FAILED {ticker}: {exc}")
                return
        else:
            log.info(
                "AGG DRY-RUN SELL %s qty=%.4f P&L=EUR%.2f (%.2f%%)",
                ticker, quantity, pnl, pnl_pct,
            )

        record = TradeRecord(
            id=None, ticker=ticker, side="SELL",
            quantity=quantity, price=price, value=value,
            sentiment="", sentiment_score=0,
            rsi=ind.rsi, bb_pband=ind.bb_pband,
            reason=reason, dry_run=cfg.dry_run,
            timestamp=datetime.utcnow().isoformat(),
            pnl=pnl, sector=buy.get("sector", ""),
            sell_type=sell_type,
            strategy_type="AGGRESSIVE",
            tp_tier=tp_tier,
        )
        self.db.insert_trade(record)

        emoji = "🚀" if pnl >= 0 else "💥"
        msg = (
            f"{'🧪 DRY' if cfg.dry_run else '🔴 LIVE'} AGG SELL {ticker} {emoji}\n"
            f"P&L: €{pnl:+.2f} ({pnl_pct:+.2f}%)\n"
            f"Type: {sell_type} (tier {tp_tier})\n"
            f"Reason: {reason}"
        )
        if self.notifier:
            self.notifier.send(msg)
