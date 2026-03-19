"""Core trading strategy v2 – AI confidence + sector rotation + smart exits.

BUY  when: AI confidence >= 75  AND  RSI < 40  AND  price at lower BB
           AND  beating S&P 500  AND  not in earnings blackout
           AND  sector allocation < 20%  AND  open positions < max
SELL when: trailing stop  OR  partial TP at +3% / full TP at +8%
           OR  AI sentiment turns NEGATIVE (immediate)
           OR  weekly rebalance
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
from bot.trading212 import Trading212Client
from bot.notifier import TelegramNotifier
from bot.sectors import (
    get_sector, get_sector_performance, should_rotate_to_defensive,
    filter_watchlist_by_rotation, sector_allocation_pct,
    DEFENSIVE_SECTORS,
)
from bot.earnings import is_in_earnings_blackout, get_earnings_info
from bot.relative_strength import calc_relative_strength, is_beating_market, clear_cache

log = logging.getLogger(__name__)

# Top 50 most-traded tickers on Trading 212 (EU-accessible US & EU blue-chips).
DEFAULT_WATCHLIST: list[str] = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "NVDA", "JPM",
    "V", "JNJ", "WMT", "PG", "UNH", "HD", "MA", "DIS", "BAC",
    "XOM", "ADBE", "CRM", "NFLX", "CSCO", "PFE", "INTC", "KO",
    "PEP", "ABT", "TMO", "MRK", "AVGO", "COST", "NKE", "CVX",
    "LLY", "ORCL", "ACN", "MCD", "MDT", "TXN", "QCOM", "AMD",
    "PYPL", "AMGN", "LOW", "IBM", "GE", "CAT", "BA", "SBUX", "UBER",
]


@dataclass
class ScanResult:
    ticker: str
    action: str  # BUY / SELL / PARTIAL_SELL / HOLD
    reason: str
    indicators: Indicators | None = None
    sentiment: AnalysisResult | None = None
    entry_price: float | None = None
    pnl_pct: float | None = None
    confidence: int = 0
    sector: str = ""
    rs_ratio: float | None = None
    earnings_blackout: bool = False
    sell_type: str = ""


class Strategy:
    """Orchestrates a full scan-and-trade cycle with v2 logic."""

    def __init__(
        self,
        t212: Trading212Client,
        news: NewsClient,
        analyzer: SentimentAnalyzer,
        db: TradeDB,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.t212 = t212
        self.news = news
        self.analyzer = analyzer
        self.db = db
        self.notifier = notifier
        self._defensive_mode = False
        self._sector_perfs: list = []

    # ── public ───────────────────────────────────────────────

    def run_full_scan(self, watchlist: list[str] | None = None) -> list[ScanResult]:
        """Run the morning scan on the watchlist. Returns per-ticker results."""
        tickers = watchlist or DEFAULT_WATCHLIST
        results: list[ScanResult] = []

        # Clear cached S&P 500 data for fresh calculations
        clear_cache()

        log.info("═══ Starting v2 scan on %d tickers ═══", len(tickers))

        # 0. Sector rotation check
        try:
            self._sector_perfs = get_sector_performance()
            self._defensive_mode = should_rotate_to_defensive(self._sector_perfs)
            if self._defensive_mode:
                log.info("🛡️ DEFENSIVE MODE ACTIVE – prioritising defensive sectors")
        except Exception as exc:
            log.warning("Sector rotation check failed: %s", exc)

        # Apply sector rotation to watchlist
        tickers = filter_watchlist_by_rotation(tickers, self._sector_perfs, self._defensive_mode)

        # 1. Check existing positions for sell signals
        sell_results = self._check_sells()
        results.extend(sell_results)

        # 2. Scan for new buy opportunities
        buy_results = self._check_buys(tickers)
        results.extend(buy_results)

        log.info(
            "═══ Scan complete: %d sells, %d partial, %d buys, %d holds ═══",
            sum(1 for r in results if r.action == "SELL"),
            sum(1 for r in results if r.action == "PARTIAL_SELL"),
            sum(1 for r in results if r.action == "BUY"),
            sum(1 for r in results if r.action == "HOLD"),
        )
        return results

    # ── sell check (v2: trailing stop, partial TP, immediate sentiment sell) ──

    def _check_sells(self) -> list[ScanResult]:
        results: list[ScanResult] = []
        open_buys = self.db.get_open_buys()

        for buy in open_buys:
            ticker = buy["ticker"]
            sector = buy.get("sector") or get_sector(ticker)
            ind = fetch_indicators(ticker)
            if not ind:
                continue

            entry_price = buy["price"]
            current_price = ind.close
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            buy_id = buy["id"]

            # ── Trailing stop logic ─────────────────────────
            trailing_high = buy.get("trailing_stop_high") or entry_price
            if current_price > trailing_high:
                trailing_high = current_price
                self.db.update_trailing_stop(buy_id, trailing_high)

            # Trailing stop triggers when price drops >stop_loss% from the high
            trailing_drop = ((trailing_high - current_price) / trailing_high) * 100
            if trailing_drop >= cfg.stop_loss_pct and pnl_pct > 0:
                reason = f"Trailing stop: dropped {trailing_drop:.1f}% from high €{trailing_high:.2f}"
                self._execute_sell(ticker, ind, buy, pnl_pct, reason, sell_type="TRAILING_SL")
                results.append(ScanResult(
                    ticker, "SELL", reason, ind, entry_price=entry_price,
                    pnl_pct=pnl_pct, sector=sector, sell_type="TRAILING_SL",
                ))
                continue

            # ── Hard stop loss (from entry) ─────────────────
            if pnl_pct <= -cfg.stop_loss_pct:
                reason = f"Stop-loss hit: {pnl_pct:+.2f}% from entry"
                self._execute_sell(ticker, ind, buy, pnl_pct, reason, sell_type="SL")
                results.append(ScanResult(
                    ticker, "SELL", reason, ind, entry_price=entry_price,
                    pnl_pct=pnl_pct, sector=sector, sell_type="SL",
                ))
                continue

            # ── Partial profit taking at +3% ────────────────
            partial_already = buy.get("partial_sold", 0)
            if pnl_pct >= cfg.partial_tp_pct and not partial_already:
                reason = f"Partial TP at {pnl_pct:+.2f}% (selling 50%)"
                self._execute_partial_sell(ticker, ind, buy, pnl_pct, reason)
                results.append(ScanResult(
                    ticker, "PARTIAL_SELL", reason, ind, entry_price=entry_price,
                    pnl_pct=pnl_pct, sector=sector, sell_type="TP_PARTIAL",
                ))
                continue

            # ── Full profit target at +8% ───────────────────
            if pnl_pct >= cfg.take_profit_pct:
                reason = f"Full take profit: {pnl_pct:+.2f}%"
                self._execute_sell(ticker, ind, buy, pnl_pct, reason, sell_type="TP_FULL")
                results.append(ScanResult(
                    ticker, "SELL", reason, ind, entry_price=entry_price,
                    pnl_pct=pnl_pct, sector=sector, sell_type="TP_FULL",
                ))
                continue

            # ── AI sentiment check (sell immediately if negative) ──
            headlines = self.news.fetch_headlines(ticker, days_back=2, max_articles=8)
            sent = self.analyzer.analyze(ticker, headlines)
            if sent.is_negative:
                reason = (
                    f"AI sentiment NEGATIVE (conf={sent.confidence}%): "
                    f"{sent.summary[:80]} – selling regardless of P&L ({pnl_pct:+.2f}%)"
                )
                self._execute_sell(ticker, ind, buy, pnl_pct, reason, sell_type="SENTIMENT")
                results.append(ScanResult(
                    ticker, "SELL", reason, ind, sent, entry_price, pnl_pct,
                    confidence=sent.confidence, sector=sector, sell_type="SENTIMENT",
                ))
                continue

            results.append(ScanResult(
                ticker, "HOLD",
                f"P&L {pnl_pct:+.2f}%, trailing high €{trailing_high:.2f}, sentiment OK",
                ind, sent, entry_price, pnl_pct,
                confidence=sent.confidence, sector=sector,
            ))

        return results

    # ── buy check (v2: confidence, earnings, RS, sector limits) ──

    def _check_buys(self, tickers: list[str]) -> list[ScanResult]:
        results: list[ScanResult] = []
        open_count = self.db.count_open_positions()
        alloc_pct = sector_allocation_pct(self.db.get_open_buys())

        for ticker in tickers:
            sector = get_sector(ticker)

            # ── Portfolio limits ─────────────────────────────
            if open_count >= cfg.max_positions:
                results.append(ScanResult(
                    ticker, "HOLD",
                    f"Max positions reached ({open_count}/{cfg.max_positions})",
                    sector=sector,
                ))
                continue

            # Skip if we already hold this stock
            if self.db.get_open_buy_for_ticker(ticker):
                continue

            # Sector allocation cap
            if alloc_pct.get(sector, 0) >= cfg.max_sector_pct:
                results.append(ScanResult(
                    ticker, "HOLD",
                    f"Sector cap: {sector} at {alloc_pct.get(sector, 0):.0f}% (max {cfg.max_sector_pct}%)",
                    sector=sector,
                ))
                continue

            # ── Earnings blackout ────────────────────────────
            in_blackout = is_in_earnings_blackout(ticker)
            if in_blackout:
                results.append(ScanResult(
                    ticker, "HOLD", f"Earnings blackout (within {cfg.earnings_blackout_days} days)",
                    sector=sector, earnings_blackout=True,
                ))
                continue

            # ── Technical indicators ─────────────────────────
            ind = fetch_indicators(ticker)
            if not ind:
                results.append(ScanResult(ticker, "HOLD", "No price data", sector=sector))
                continue

            if not ind.rsi_oversold or not ind.at_lower_bb:
                reason_parts = []
                if not ind.rsi_oversold:
                    reason_parts.append(f"RSI={ind.rsi:.1f} (need <40)")
                if not ind.at_lower_bb:
                    reason_parts.append(f"BB%={ind.bb_pband:.2f} (not at lower)")
                reason = "Technicals not met: " + ", ".join(reason_parts)
                self.db.log_scan(
                    ticker, "SKIPPED", 0, ind.rsi, ind.macd_hist, ind.bb_pband,
                    "HOLD", sector=sector,
                )
                results.append(ScanResult(ticker, "HOLD", reason, ind, sector=sector))
                continue

            # ── Relative strength vs S&P 500 ────────────────
            rs = calc_relative_strength(ticker, period="1mo")
            rs_ratio = rs.rs_ratio if rs else None
            if rs and not rs.beating_market:
                reason = f"Underperforming S&P 500: stock {rs.stock_return:+.1f}% vs SPX {rs.sp500_return:+.1f}%"
                self.db.log_scan(
                    ticker, "SKIPPED", 0, ind.rsi, ind.macd_hist, ind.bb_pband,
                    "HOLD", rs_vs_sp500=rs.rs_ratio, sector=sector,
                )
                results.append(ScanResult(
                    ticker, "HOLD", reason, ind, sector=sector, rs_ratio=rs_ratio,
                ))
                continue

            # ── AI Analysis (only if all other filters pass) ──
            headlines = self.news.fetch_headlines(ticker, days_back=3, max_articles=10)
            sent = self.analyzer.analyze(ticker, headlines)

            self.db.log_scan(
                ticker, sent.sentiment, sent.sentiment_score,
                ind.rsi, ind.macd_hist, ind.bb_pband,
                "BUY" if sent.bullish else "HOLD",
                confidence=sent.confidence,
                rs_vs_sp500=rs_ratio or 0,
                sector=sector,
            )

            # Buy requires: POSITIVE sentiment AND confidence >= 75
            if sent.bullish:
                pt_str = f", PT €{sent.price_target:.2f}" if sent.price_target else ""
                reason = (
                    f"BUY: conf={sent.confidence}% {sent.sentiment}"
                    f" RSI={ind.rsi:.1f} BB%={ind.bb_pband:.2f}"
                    f" earnings={sent.earnings_outlook} val={sent.valuation}"
                    f" analyst={sent.analyst_consensus}{pt_str}"
                )
                self._execute_buy(ticker, ind, sent, reason, sector)
                open_count += 1
                # Update allocation
                alloc_pct = sector_allocation_pct(self.db.get_open_buys())
                results.append(ScanResult(
                    ticker, "BUY", reason, ind, sent,
                    confidence=sent.confidence, sector=sector, rs_ratio=rs_ratio,
                ))
            else:
                if sent.confidence < 75:
                    reason = f"Confidence too low: {sent.confidence}% (need >=75)"
                else:
                    reason = f"Sentiment not positive: {sent.sentiment} ({sent.sentiment_score:+.2f})"
                results.append(ScanResult(
                    ticker, "HOLD", reason, ind, sent,
                    confidence=sent.confidence, sector=sector, rs_ratio=rs_ratio,
                ))

        return results

    # ── execution ────────────────────────────────────────────

    def _execute_buy(
        self, ticker: str, ind: Indicators, sent: AnalysisResult,
        reason: str, sector: str,
    ) -> None:
        price = ind.close
        value = cfg.order_size_eur
        quantity = value / price if price > 0 else 0

        if not cfg.dry_run:
            try:
                self.t212.place_value_order(ticker, value)
                log.info("✅ LIVE BUY  %s  €%.2f", ticker, value)
            except Exception as exc:
                log.error("❌ Order failed for %s: %s", ticker, exc)
                if self.notifier:
                    self.notifier.send(f"❌ BUY order FAILED for {ticker}: {exc}")
                return
        else:
            log.info("🧪 DRY-RUN BUY  %s  €%.2f  (%.4f shares @ %.2f)", ticker, value, quantity, price)

        record = TradeRecord(
            id=None, ticker=ticker, side="BUY",
            quantity=quantity, price=price, value=value,
            sentiment=sent.sentiment, sentiment_score=sent.sentiment_score,
            rsi=ind.rsi, bb_pband=ind.bb_pband,
            reason=reason, dry_run=cfg.dry_run,
            timestamp=datetime.utcnow().isoformat(),
            sector=sector, confidence=sent.confidence,
            price_target=sent.price_target,
            price_target_timeframe=sent.price_target_timeframe or "",
            trailing_stop_high=price,
        )
        self.db.insert_trade(record)

        pt_str = f"\nPrice Target: €{sent.price_target:.2f} ({sent.price_target_timeframe})" if sent.price_target else ""
        msg = (
            f"{'🧪 DRY' if cfg.dry_run else '✅ LIVE'} BUY {ticker} ({sector})\n"
            f"€{value:.2f} ({quantity:.4f} shares @ €{price:.2f})\n"
            f"AI Confidence: {sent.confidence}%  Sentiment: {sent.sentiment}\n"
            f"RSI={ind.rsi:.1f}  BB%={ind.bb_pband:.2f}\n"
            f"Earnings: {sent.earnings_outlook}  Valuation: {sent.valuation}\n"
            f"Analyst: {sent.analyst_consensus}{pt_str}\n"
            f"{sent.summary[:200]}"
        )
        if self.notifier:
            self.notifier.send(msg)

    def _execute_partial_sell(
        self, ticker: str, ind: Indicators, buy: dict,
        pnl_pct: float, reason: str,
    ) -> None:
        """Sell 50% of position at partial TP, let the rest ride."""
        price = ind.close
        full_qty = buy["quantity"]
        sell_qty = full_qty * 0.5
        sell_value = sell_qty * price
        buy_half_value = buy["value"] * 0.5
        pnl = sell_value - buy_half_value

        if not cfg.dry_run:
            try:
                self.t212.place_market_order(ticker, -abs(sell_qty))
                log.info("✅ LIVE PARTIAL SELL  %s  qty=%.4f (50%%)", ticker, sell_qty)
            except Exception as exc:
                log.error("❌ Partial sell failed for %s: %s", ticker, exc)
                if self.notifier:
                    self.notifier.send(f"❌ PARTIAL SELL failed for {ticker}: {exc}")
                return
        else:
            log.info("🧪 DRY-RUN PARTIAL SELL  %s  qty=%.4f  P&L=€%.2f", ticker, sell_qty, pnl)

        # Mark the original buy as partially sold
        self.db.mark_partial_sold(buy["id"])

        record = TradeRecord(
            id=None, ticker=ticker, side="PARTIAL_SELL",
            quantity=sell_qty, price=price, value=sell_value,
            sentiment="", sentiment_score=0,
            rsi=ind.rsi, bb_pband=ind.bb_pband,
            reason=reason, dry_run=cfg.dry_run,
            timestamp=datetime.utcnow().isoformat(),
            pnl=pnl, sector=buy.get("sector", ""),
            sell_type="TP_PARTIAL",
        )
        self.db.insert_trade(record)

        msg = (
            f"{'🧪 DRY' if cfg.dry_run else '✅ LIVE'} PARTIAL SELL {ticker} (50%)\n"
            f"Sold {sell_qty:.4f} @ €{price:.2f} = €{sell_value:.2f}\n"
            f"P&L on sold portion: €{pnl:+.2f} ({pnl_pct:+.2f}%)\n"
            f"Remaining position rides to +{cfg.take_profit_pct}%"
        )
        if self.notifier:
            self.notifier.send(msg)

    def _execute_sell(
        self, ticker: str, ind: Indicators, buy: dict,
        pnl_pct: float, reason: str, sell_type: str = "",
    ) -> None:
        price = ind.close
        quantity = buy["quantity"]
        # If partially sold, only sell remaining half
        if buy.get("partial_sold"):
            quantity = quantity * 0.5
        value = quantity * price
        buy_value = buy["value"] if not buy.get("partial_sold") else buy["value"] * 0.5
        pnl = value - buy_value

        if not cfg.dry_run:
            try:
                pos = self.t212.get_position(ticker)
                if pos:
                    sell_qty = pos.get("quantity", quantity)
                    self.t212.place_market_order(ticker, -abs(sell_qty))
                    log.info("✅ LIVE SELL  %s  qty=%.4f", ticker, sell_qty)
                else:
                    log.warning("No T212 position found for %s", ticker)
            except Exception as exc:
                log.error("❌ Sell order failed for %s: %s", ticker, exc)
                if self.notifier:
                    self.notifier.send(f"❌ SELL order FAILED for {ticker}: {exc}")
                return
        else:
            log.info("🧪 DRY-RUN SELL  %s  qty=%.4f  P&L=€%.2f (%.2f%%)", ticker, quantity, pnl, pnl_pct)

        record = TradeRecord(
            id=None, ticker=ticker, side="SELL",
            quantity=quantity, price=price, value=value,
            sentiment="", sentiment_score=0,
            rsi=ind.rsi, bb_pband=ind.bb_pband,
            reason=reason, dry_run=cfg.dry_run,
            timestamp=datetime.utcnow().isoformat(),
            pnl=pnl, sector=buy.get("sector", ""),
            sell_type=sell_type,
        )
        self.db.insert_trade(record)

        emoji = "📈" if pnl >= 0 else "📉"
        msg = (
            f"{'🧪 DRY' if cfg.dry_run else '✅ LIVE'} SELL {ticker}  {emoji}\n"
            f"P&L: €{pnl:+.2f} ({pnl_pct:+.2f}%)\n"
            f"Type: {sell_type}\n"
            f"Reason: {reason}"
        )
        if self.notifier:
            self.notifier.send(msg)

    # ── weekly rebalance ─────────────────────────────────────

    def run_rebalance(self) -> list[ScanResult]:
        """Weekly rebalance: sell positions in over-allocated sectors."""
        results: list[ScanResult] = []
        open_buys = self.db.get_open_buys()
        alloc = sector_allocation_pct(open_buys)

        for sector, pct in alloc.items():
            if pct > cfg.max_sector_pct:
                # Find the worst-performing position in this sector
                sector_positions = [
                    b for b in open_buys if get_sector(b["ticker"]) == sector
                ]
                if not sector_positions:
                    continue

                # Sort by P&L (worst first)
                for pos in sector_positions:
                    ind = fetch_indicators(pos["ticker"])
                    if not ind:
                        continue
                    pnl_pct = ((ind.close - pos["price"]) / pos["price"]) * 100
                    pos["_current_pnl"] = pnl_pct

                sector_positions.sort(key=lambda p: p.get("_current_pnl", 0))

                # Sell the worst performer to bring sector down
                worst = sector_positions[0]
                ind = fetch_indicators(worst["ticker"])
                if ind:
                    pnl_pct = worst.get("_current_pnl", 0)
                    reason = f"Rebalance: {sector} at {pct:.0f}% (max {cfg.max_sector_pct}%)"
                    self._execute_sell(worst["ticker"], ind, worst, pnl_pct, reason, sell_type="REBALANCE")
                    results.append(ScanResult(
                        worst["ticker"], "SELL", reason, ind,
                        pnl_pct=pnl_pct, sector=sector, sell_type="REBALANCE",
                    ))
                    log.info("Rebalanced: sold %s from %s sector", worst["ticker"], sector)

        return results
