"""Beautiful terminal dashboard v3 powered by Rich.

Renders a live-updating panel showing:
  * Bot status & next scan time
  * Open positions with trailing stops and sector info
  * Recent scan results with AI confidence & catalyst scores
  * Trade history with realised P&L
  * Sector allocation
  * Performance analytics (Sharpe, drawdown, win rate by sector)
  * AI confidence performance
  * Aggressive strategy momentum & catalyst info
"""

from __future__ import annotations

import logging
from datetime import datetime

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bot.database import TradeDB
from bot.strategy import ScanResult
from bot.analytics import compute_analytics, AnalyticsReport
from bot.sectors import get_sector, sector_allocation_pct

log = logging.getLogger(__name__)
console = Console()


def _color_pnl(val: float | None) -> Text:
    if val is None:
        return Text("—", style="dim")
    style = "bold green" if val >= 0 else "bold red"
    return Text(f"€{val:+.2f}", style=style)


def _color_pct(val: float | None) -> Text:
    if val is None:
        return Text("—", style="dim")
    style = "bold green" if val >= 0 else "bold red"
    return Text(f"{val:+.2f}%", style=style)


def _confidence_bar(conf: int) -> Text:
    """Visual confidence indicator."""
    if conf >= 85:
        return Text(f"{'█' * 5} {conf}%", style="bold green")
    elif conf >= 75:
        return Text(f"{'█' * 4}░ {conf}%", style="green")
    elif conf >= 50:
        return Text(f"{'█' * 3}░░ {conf}%", style="yellow")
    elif conf >= 25:
        return Text(f"{'█' * 2}░░░ {conf}%", style="dark_orange3")
    else:
        return Text(f"{'█' * 1}░░░░ {conf}%", style="red")


def _sentiment_badge(sent: str) -> Text:
    colors = {"POSITIVE": "green", "NEGATIVE": "red", "NEUTRAL": "yellow"}
    return Text(f" {sent} ", style=f"bold white on {colors.get(sent, 'dim')}")


class Dashboard:
    """Builds Rich renderables for the terminal UI."""

    def __init__(self, db: TradeDB) -> None:
        self.db = db
        self._last_scan_results: list[ScanResult] = []
        self._next_scan: str = "—"
        self._status: str = "Idle"
        self._dry_run: bool = True
        self._defensive_mode: bool = False
        self._analytics: AnalyticsReport | None = None

    def set_scan_results(self, results: list[ScanResult]) -> None:
        self._last_scan_results = results
        # Refresh analytics on each scan
        try:
            self._analytics = compute_analytics(self.db)
        except Exception:
            pass

    def set_next_scan(self, when: str) -> None:
        self._next_scan = when

    def set_status(self, status: str) -> None:
        self._status = status

    def set_dry_run(self, val: bool) -> None:
        self._dry_run = val

    def set_defensive_mode(self, val: bool) -> None:
        self._defensive_mode = val

    # ── panels ───────────────────────────────────────────────

    def _header_panel(self) -> Panel:
        mode = Text(" 🧪 DRY-RUN ", style="bold white on dark_orange3") if self._dry_run \
            else Text(" 🔴 LIVE TRADING ", style="bold white on red")
        defense = Text(" 🛡️ DEFENSIVE ", style="bold white on blue") if self._defensive_mode \
            else Text(" ⚡ NORMAL ", style="bold white on dark_green")
        grid = Table.grid(padding=1)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_row(
            Text("StockBot AI v3", style="bold cyan"),
            mode,
            defense,
            Text(f"Next scan: {self._next_scan}", style="dim"),
        )
        open_count = self.db.count_open_positions()
        safe_count = self.db.count_open_positions("SAFE")
        agg_count = self.db.count_open_positions("AGGRESSIVE")
        grid.add_row(
            Text(f"Status: {self._status}", style="italic"),
            Text(f"Positions: {open_count} (S:{safe_count} A:{agg_count})", style="bold"),
            Text(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
            Text(""),
        )
        return Panel(grid, title="[bold cyan]StockBot AI v3[/bold cyan]", border_style="cyan")

    def _open_positions_table(self) -> Panel:
        table = Table(show_header=True, header_style="bold magenta", expand=True, show_lines=False)
        table.add_column("Ticker", style="bold")
        table.add_column("Type", width=3)
        table.add_column("Sector", style="dim")
        table.add_column("Entry", justify="right")
        table.add_column("Qty", justify="right")
        table.add_column("Conf", justify="right")
        table.add_column("Cat", justify="right")
        table.add_column("Trail", justify="right")
        table.add_column("TP Tier", justify="center")
        table.add_column("Opened", style="dim")

        open_buys = self.db.get_open_buys()
        if not open_buys:
            table.add_row("--", "--", "--", "--", "--", "--", "--", "--", "--", "No open positions")
        for b in open_buys:
            conf = b.get("confidence", 0) or 0
            cat = b.get("catalyst_score", 0) or 0
            trail = b.get("trailing_stop_high")
            strat = b.get("strategy_type", "SAFE")
            tp_tier = b.get("tp_tier", 0) or 0
            strat_badge = Text(" S ", style="bold white on dark_green") if strat == "SAFE" \
                else Text(" A ", style="bold white on red")
            cat_text = Text(f"{cat}/10", style="bold yellow") if cat > 0 else Text("--", style="dim")
            tp_text = Text(f"T{tp_tier}", style="bold cyan") if tp_tier > 0 else Text("--", style="dim")
            table.add_row(
                b["ticker"],
                strat_badge,
                (b.get("sector") or get_sector(b["ticker"]))[:12],
                f"{b['price']:.2f}",
                f"{b['quantity']:.4f}",
                _confidence_bar(conf),
                cat_text,
                f"{trail:.2f}" if trail else "--",
                tp_text,
                (b.get("timestamp") or "")[:16],
            )
        return Panel(table, title="[bold magenta]Open Positions[/bold magenta]", border_style="magenta")

    def _scan_results_table(self) -> Panel:
        table = Table(show_header=True, header_style="bold blue", expand=True, show_lines=False)
        table.add_column("Ticker", style="bold")
        table.add_column("Type", width=3)
        table.add_column("Action")
        table.add_column("Conf", justify="right")
        table.add_column("Cat", justify="right")
        table.add_column("Mom", justify="right")
        table.add_column("RSI", justify="right")
        table.add_column("Sector", style="dim")
        table.add_column("Reason", max_width=40)

        if not self._last_scan_results:
            table.add_row("--", "--", "--", "--", "--", "--", "--", "--", "Awaiting first scan...")
        for r in self._last_scan_results[:25]:
            action_colors = {
                "BUY": "bold green", "SELL": "bold red",
                "PARTIAL_SELL": "bold yellow", "HOLD": "dim",
            }
            action_style = action_colors.get(r.action, "")
            ind = r.indicators
            strat = getattr(r, 'strategy_type', 'SAFE')
            strat_badge = Text("S", style="green") if strat == "SAFE" else Text("A", style="red")
            cat = getattr(r, 'catalyst_score', 0)
            mom = getattr(r, 'momentum_score', 0)
            cat_text = Text(f"{cat}", style="bold yellow") if cat > 0 else Text("--", style="dim")
            mom_text = Text(f"{mom:.0f}", style="bold cyan") if mom > 0 else Text("--", style="dim")
            table.add_row(
                r.ticker,
                strat_badge,
                Text(r.action, style=action_style),
                _confidence_bar(r.confidence) if r.confidence else Text("--", style="dim"),
                cat_text,
                mom_text,
                f"{ind.rsi:.1f}" if ind else "--",
                r.sector[:10] if r.sector else "--",
                r.reason[:40],
            )
        return Panel(table, title="[bold blue]Last Scan Results[/bold blue]", border_style="blue")

    def _trade_history_table(self) -> Panel:
        table = Table(show_header=True, header_style="bold yellow", expand=True, show_lines=False)
        table.add_column("ID", style="dim")
        table.add_column("Ticker", style="bold")
        table.add_column("Str", width=3)
        table.add_column("Side")
        table.add_column("Type", style="dim")
        table.add_column("Price", justify="right")
        table.add_column("P&L")
        table.add_column("Cat", justify="right")
        table.add_column("Dry?", justify="center")
        table.add_column("Time", style="dim")

        trades = self.db.get_all_trades(limit=12)
        for t in trades:
            side_colors = {"BUY": "green", "SELL": "red", "PARTIAL_SELL": "yellow"}
            side_style = side_colors.get(t["side"], "")
            strat = t.get("strategy_type", "SAFE")
            strat_text = Text("S", style="green") if strat == "SAFE" else Text("A", style="red")
            cat = t.get("catalyst_score", 0) or 0
            cat_text = Text(str(cat), style="yellow") if cat > 0 else Text("--", style="dim")
            table.add_row(
                str(t["id"]),
                t["ticker"],
                strat_text,
                Text(t["side"], style=side_style),
                (t.get("sell_type") or "")[:10],
                f"{t['price']:.2f}",
                _color_pnl(t.get("pnl")),
                cat_text,
                "dry" if t.get("dry_run") else "LIVE",
                (t.get("timestamp") or "")[:16],
            )
        return Panel(table, title="[bold yellow]Trade History[/bold yellow]", border_style="yellow")

    def _sector_allocation_panel(self) -> Panel:
        open_buys = self.db.get_open_buys()
        alloc = sector_allocation_pct(open_buys)

        if not alloc:
            return Panel(Text("No open positions", style="dim"), title="Sector Allocation", border_style="cyan")

        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("Sector")
        table.add_column("Alloc %", justify="right")
        table.add_column("Bar")
        table.add_column("Status")

        for sector, pct in sorted(alloc.items(), key=lambda x: -x[1]):
            bar_len = int(pct / 5)  # 20% = 4 blocks
            bar = "█" * bar_len + "░" * (4 - bar_len)
            style = "red" if pct >= 20 else "green"
            status = "⚠️ CAP" if pct >= 20 else "✅ OK"
            table.add_row(sector[:15], Text(f"{pct:.1f}%", style=style), bar, status)

        return Panel(table, title="[bold cyan]🏢 Sector Allocation[/bold cyan]", border_style="cyan")

    def _analytics_panel(self) -> Panel:
        a = self._analytics
        if not a:
            # Try to compute
            try:
                a = compute_analytics(self.db)
                self._analytics = a
            except Exception:
                return Panel("No data yet", title="Analytics", border_style="green")

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column()
        grid.add_column(style="bold")
        grid.add_column()

        grid.add_row(
            "Total Trades:", str(a.total_trades),
            "Win Rate:", Text(f"{a.win_rate:.1f}%", style="bold green" if a.win_rate >= 50 else "bold red"),
        )
        grid.add_row(
            "Total P&L:", _color_pnl(a.total_pnl),
            "Profit Factor:", Text(f"{a.profit_factor:.2f}" if a.profit_factor < 100 else "∞", style="bold"),
        )
        grid.add_row(
            "Avg Win:", _color_pnl(a.avg_win),
            "Avg Loss:", _color_pnl(a.avg_loss),
        )
        grid.add_row(
            "Sharpe:", Text(f"{a.sharpe_ratio:.3f}" if a.sharpe_ratio is not None else "N/A", style="bold"),
            "Max DD:", Text(f"{a.max_drawdown_pct:.1f}% (€{a.max_drawdown_eur:.2f})", style="red" if a.max_drawdown_pct > 10 else ""),
        )

        # Best / worst trade
        if a.best_trade:
            best_pnl = a.best_trade.get("pnl", 0) or 0
            grid.add_row(
                "Best:", Text(f"{a.best_trade['ticker']} €{best_pnl:+.2f}", style="green"),
                "", "",
            )
        if a.worst_trade:
            worst_pnl = a.worst_trade.get("pnl", 0) or 0
            grid.add_row(
                "Worst:", Text(f"{a.worst_trade['ticker']} €{worst_pnl:+.2f}", style="red"),
                "", "",
            )

        return Panel(grid, title="[bold green]📊 Performance Analytics[/bold green]", border_style="green")

    def _sector_stats_panel(self) -> Panel:
        a = self._analytics
        if not a or not a.sector_stats:
            return Panel("No sector data yet", title="Win Rate by Sector", border_style="yellow")

        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("Sector")
        table.add_column("Trades", justify="right")
        table.add_column("W/L", justify="right")
        table.add_column("Win %", justify="right")
        table.add_column("P&L")

        for s in sorted(a.sector_stats.values(), key=lambda x: -x.total_pnl):
            wr_style = "green" if s.win_rate >= 50 else "red"
            table.add_row(
                s.sector[:15],
                str(s.trades),
                f"{s.wins}/{s.losses}",
                Text(f"{s.win_rate:.0f}%", style=wr_style),
                _color_pnl(s.total_pnl),
            )

        return Panel(table, title="[bold yellow]🏆 Win Rate by Sector[/bold yellow]", border_style="yellow")

    def _confidence_stats_panel(self) -> Panel:
        a = self._analytics
        if not a or not a.confidence_buckets:
            return Panel("No confidence data yet", title="AI Confidence Performance", border_style="magenta")

        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("Confidence")
        table.add_column("Trades", justify="right")
        table.add_column("Win %", justify="right")
        table.add_column("Avg P&L")
        table.add_column("Total P&L")

        for b in a.confidence_buckets.values():
            if b.trades == 0:
                continue
            wr_style = "green" if b.win_rate >= 50 else "red"
            table.add_row(
                b.bucket_label,
                str(b.trades),
                Text(f"{b.win_rate:.0f}%", style=wr_style),
                _color_pnl(b.avg_pnl),
                _color_pnl(b.total_pnl),
            )

        return Panel(table, title="[bold magenta]🧠 AI Confidence Performance[/bold magenta]", border_style="magenta")

    # ── full layout ──────────────────────────────────────────

    def build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=14),
        )

        # Body: left (scans + positions) | right (analytics stack)
        layout["body"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )
        layout["left"].split_column(
            Layout(self._scan_results_table(), name="scans", ratio=1),
            Layout(self._open_positions_table(), name="positions", ratio=1),
        )
        layout["right"].split_column(
            Layout(self._analytics_panel(), name="analytics", ratio=2),
            Layout(name="right_bottom", ratio=3),
        )
        layout["right_bottom"].split_column(
            Layout(self._sector_allocation_panel(), name="sectors", ratio=1),
            Layout(self._sector_stats_panel(), name="sector_stats", ratio=1),
            Layout(self._confidence_stats_panel(), name="confidence", ratio=1),
        )

        layout["header"].update(self._header_panel())
        layout["footer"].update(self._trade_history_table())

        return layout

    def print_static(self) -> None:
        """Print the dashboard once (non-live mode)."""
        # Ensure analytics are computed
        try:
            self._analytics = compute_analytics(self.db)
        except Exception:
            pass
        console.print(self.build_layout())

    def get_live(self, refresh_per_second: int = 1) -> Live:
        """Return a Rich Live context manager for continuous refresh."""
        return Live(self.build_layout(), console=console, refresh_per_second=refresh_per_second)
