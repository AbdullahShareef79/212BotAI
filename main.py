#!/usr/bin/env python3
"""
StockBot AI v2 – AI-powered stock trading bot for Trading 212.

Usage:
    python main.py                Run in scheduled mode (scans at market open)
    python main.py --scan-now     Run a single scan immediately
    python main.py --rebalance    Run portfolio rebalance immediately
    python main.py --analytics    Show performance analytics and exit
    python main.py --dashboard    Show dashboard without scanning
    python main.py --dry-run      Override .env and force dry-run
    python main.py --live         Override .env and force live mode (⚠ real money!)
"""

from __future__ import annotations

# ── Force UTF-8 output on Windows before Rich touches stdout ──
import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse
import logging
import time
from datetime import datetime

from rich.console import Console
from rich.logging import RichHandler

# ── bootstrap logging BEFORE importing project modules ──────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)],
)
log = logging.getLogger("stockbot")

# ── project imports ─────────────────────────────────────────
from config import cfg  # noqa: E402 (must come after logging setup)
from bot.trading212 import Trading212Client  # noqa: E402
from bot.news import NewsClient  # noqa: E402
from bot.sentiment import SentimentAnalyzer  # noqa: E402
from bot.database import TradeDB  # noqa: E402
from bot.strategy import Strategy, DEFAULT_WATCHLIST  # noqa: E402
from bot.dashboard import Dashboard  # noqa: E402
from bot.notifier import TelegramNotifier  # noqa: E402
from bot.scheduler import setup_schedule, run_loop, next_run_str  # noqa: E402
from bot.analytics import compute_analytics  # noqa: E402

console = Console()

# ── banner ──────────────────────────────────────────────────
BANNER = """
[bold cyan]
  +-------------------------------------------------------+
  |  StockBot AI v2                                       |
  |  AI-Powered Trading  *  Trading 212  *  GPT-4o-mini   |
  +-------------------------------------------------------+
[/bold cyan]
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="StockBot AI v2 – Trading 212 bot")
    p.add_argument("--scan-now", action="store_true", help="Run a single scan immediately and exit")
    p.add_argument("--rebalance", action="store_true", help="Run portfolio rebalance and exit")
    p.add_argument("--analytics", action="store_true", help="Show performance analytics and exit")
    p.add_argument("--dashboard", action="store_true", help="Show dashboard only (no scan)")
    p.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    p.add_argument("--live", action="store_true", help="Force live mode (real money!)")
    p.add_argument("--watchlist", nargs="*", help="Override default watchlist with custom tickers")
    return p.parse_args()


def build_components(force_dry: bool = False, force_live: bool = False):
    """Instantiate all service objects."""
    # Dry-run override
    import config as _cfg_mod
    if force_dry:
        object.__setattr__(_cfg_mod.cfg, "dry_run", True)
    if force_live:
        object.__setattr__(_cfg_mod.cfg, "dry_run", False)

    t212 = Trading212Client(cfg.trading212_api_key, cfg.trading212_base_url)
    news = NewsClient(cfg.newsapi_key)
    analyzer = SentimentAnalyzer(cfg.openai_api_key, model="gpt-4o-mini")
    db = TradeDB(cfg.db_path)

    notifier = None
    if cfg.telegram_enabled:
        notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
        log.info("Telegram notifications enabled")
    else:
        log.info("Telegram not configured – notifications disabled")

    strategy = Strategy(t212, news, analyzer, db, notifier)
    dashboard = Dashboard(db)
    dashboard.set_dry_run(cfg.dry_run)

    return t212, news, analyzer, db, strategy, dashboard, notifier


def single_scan(strategy: Strategy, dashboard: Dashboard, watchlist: list[str]) -> None:
    """Execute one full scan cycle."""
    dashboard.set_status("🔄 Scanning…")
    start = time.time()

    results = strategy.run_full_scan(watchlist)

    elapsed = time.time() - start
    dashboard.set_scan_results(results)
    dashboard.set_status(f"✅ Last scan completed in {elapsed:.1f}s")

    buys = sum(1 for r in results if r.action == "BUY")
    sells = sum(1 for r in results if r.action == "SELL")
    log.info("Scan finished in %.1fs – %d BUY, %d SELL, %d HOLD", elapsed, buys, sells, len(results) - buys - sells)


def main() -> None:
    console.print(BANNER)
    args = parse_args()

    try:
        t212, news, analyzer, db, strategy, dashboard, notifier = build_components(
            force_dry=args.dry_run, force_live=args.live,
        )
    except EnvironmentError as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        console.print("Copy .env.example → .env and fill in your API keys.")
        sys.exit(1)

    watchlist = args.watchlist or DEFAULT_WATCHLIST
    mode_str = "🧪 DRY-RUN" if cfg.dry_run else "🔴 LIVE"
    console.print(f"  Mode: [bold]{mode_str}[/bold]   Watchlist: {len(watchlist)} tickers")
    console.print(
        f"  Max Positions: {cfg.max_positions}   Sector Cap: {cfg.max_sector_pct}%"
        f"   Min Confidence: {cfg.min_confidence}%"
    )
    console.print(
        f"  TP: +{cfg.take_profit_pct}% (partial at +{cfg.partial_tp_pct}%)"
        f"   SL: -{cfg.stop_loss_pct}% (trailing)\n"
    )

    # ── Mode: analytics ─────────────────────────────────────
    if args.analytics:
        _show_analytics(db)
        return

    # ── Mode: rebalance ─────────────────────────────────────
    if args.rebalance:
        results = strategy.run_rebalance()
        if results:
            dashboard.set_scan_results(results)
            console.print(f"[bold]Rebalanced {len(results)} positions[/bold]")
        else:
            console.print("[dim]No rebalancing needed[/dim]")
        dashboard.print_static()
        return

    # ── Mode: single scan ───────────────────────────────────
    if args.scan_now:
        single_scan(strategy, dashboard, watchlist)
        dashboard.print_static()
        return

    # ── Mode: dashboard only ────────────────────────────────
    if args.dashboard:
        dashboard.set_status("Dashboard-only mode")
        dashboard.print_static()
        return

    # ── Mode: scheduled ─────────────────────────────────────
    def on_scan():
        single_scan(strategy, dashboard, watchlist)
        dashboard.set_next_scan(next_run_str(cfg.timezone))
        # Weekly rebalance on configured day
        if datetime.now().strftime("%A").lower() == cfg.rebalance_day.lower():
            log.info("📅 Weekly rebalance day – running rebalance")
            rebalance_results = strategy.run_rebalance()
            if rebalance_results and notifier:
                notifier.send(f"📅 Weekly rebalance: {len(rebalance_results)} positions adjusted")

    setup_schedule(cfg.scan_hour, cfg.scan_minute, cfg.timezone, on_scan)
    dashboard.set_next_scan(next_run_str(cfg.timezone))
    dashboard.set_status(f"Waiting for {cfg.scan_hour:02d}:{cfg.scan_minute:02d} {cfg.timezone}")

    if notifier:
        notifier.send(
            f"🤖 StockBot v2 started ({mode_str})\n"
            f"Next scan: {next_run_str(cfg.timezone)}\n"
            f"Watching {len(watchlist)} tickers\n"
            f"Max positions: {cfg.max_positions} | Min confidence: {cfg.min_confidence}%"
        )

    # Live dashboard with scheduler
    with dashboard.get_live(refresh_per_second=1) as live:
        try:
            import schedule as _sched
            while True:
                _sched.run_pending()
                live.update(dashboard.build_layout())
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down…[/yellow]")
        finally:
            t212.close()
            news.close()
            if notifier:
                notifier.send("🛑 StockBot stopped")
                notifier.close()

    console.print("[bold green]StockBot exited cleanly.[/bold green]")


def _show_analytics(db: TradeDB) -> None:
    """Print a detailed analytics report to the terminal."""
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

    report = compute_analytics(db)

    console.print(Panel.fit(
        f"[bold]Total Trades:[/bold] {report.total_trades}   "
        f"[bold]Win Rate:[/bold] {report.win_rate:.1f}%   "
        f"[bold]P&L:[/bold] €{report.total_pnl:+.2f}",
        title="📊 Performance Summary",
    ))

    # Risk metrics
    console.print(Panel.fit(
        f"[bold]Sharpe Ratio:[/bold] {report.sharpe_ratio:.3f if report.sharpe_ratio else 'N/A'}   "
        f"[bold]Max Drawdown:[/bold] {report.max_drawdown_pct:.1f}% (€{report.max_drawdown_eur:.2f})   "
        f"[bold]Profit Factor:[/bold] {report.profit_factor:.2f}",
        title="⚡ Risk Metrics",
    ))

    # Sector stats
    if report.sector_stats:
        table = Table(title="🏆 Win Rate by Sector", show_header=True)
        table.add_column("Sector")
        table.add_column("Trades", justify="right")
        table.add_column("W/L", justify="right")
        table.add_column("Win %", justify="right")
        table.add_column("Total P&L", justify="right")
        table.add_column("Avg P&L", justify="right")
        for s in sorted(report.sector_stats.values(), key=lambda x: -x.total_pnl):
            table.add_row(
                s.sector, str(s.trades), f"{s.wins}/{s.losses}",
                f"{s.win_rate:.0f}%", f"€{s.total_pnl:+.2f}", f"€{s.avg_pnl:+.2f}",
            )
        console.print(table)

    # Confidence buckets
    if report.confidence_buckets:
        table = Table(title="🧠 AI Confidence Performance", show_header=True)
        table.add_column("Confidence Range")
        table.add_column("Trades", justify="right")
        table.add_column("Win %", justify="right")
        table.add_column("Avg P&L", justify="right")
        table.add_column("Total P&L", justify="right")
        for b in report.confidence_buckets.values():
            if b.trades == 0:
                continue
            table.add_row(
                b.bucket_label, str(b.trades),
                f"{b.win_rate:.0f}%", f"€{b.avg_pnl:+.2f}", f"€{b.total_pnl:+.2f}",
            )
        console.print(table)


if __name__ == "__main__":
    main()
