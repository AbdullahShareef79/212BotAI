"""SQLite persistence layer for trade tracking and audit log.

v2: Added sector, confidence, price_target, trailing_stop_high,
    partial_sold, sell_type columns for the upgraded strategy.
v3: Added strategy_type, catalyst_score, catalyst_type,
    position_size_pct, tp_tier for aggressive/hybrid strategy.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generator

log = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    id: int | None
    ticker: str
    side: str  # BUY / SELL / PARTIAL_SELL
    quantity: float
    price: float
    value: float
    sentiment: str
    sentiment_score: float
    rsi: float
    bb_pband: float
    reason: str
    dry_run: bool
    timestamp: str
    pnl: float | None = None  # set on sell
    # v2 fields
    sector: str = ""
    confidence: int = 0
    price_target: float | None = None
    price_target_timeframe: str = ""
    trailing_stop_high: float | None = None
    partial_sold: bool = False
    sell_type: str = ""  # TP_PARTIAL / TP_FULL / SL / TRAILING_SL / SENTIMENT / REBALANCE
    # v3 fields
    strategy_type: str = "SAFE"  # SAFE / AGGRESSIVE
    catalyst_score: int = 0
    catalyst_type: str = ""
    position_size_pct: float = 0.0
    tp_tier: int = 0  # 0=none, 1=+15%, 2=+30%, 3=+50%


# Step 1: create tables (no indexes – they may reference columns not yet migrated)
_SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS trades (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                TEXT    NOT NULL,
    side                  TEXT    NOT NULL,
    quantity              REAL    NOT NULL,
    price                 REAL    NOT NULL,
    value                 REAL    NOT NULL,
    sentiment             TEXT,
    sentiment_score       REAL,
    rsi                   REAL,
    bb_pband              REAL,
    reason                TEXT,
    dry_run               INTEGER DEFAULT 1,
    pnl                   REAL,
    timestamp             TEXT    NOT NULL,
    sector                TEXT    DEFAULT '',
    confidence            INTEGER DEFAULT 0,
    price_target          REAL,
    price_target_timeframe TEXT   DEFAULT '',
    trailing_stop_high    REAL,
    partial_sold          INTEGER DEFAULT 0,
    sell_type             TEXT    DEFAULT '',
    strategy_type         TEXT    DEFAULT 'SAFE',
    catalyst_score        INTEGER DEFAULT 0,
    catalyst_type         TEXT    DEFAULT '',
    position_size_pct     REAL    DEFAULT 0.0,
    tp_tier               INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    sentiment       TEXT,
    sentiment_score REAL,
    confidence      INTEGER DEFAULT 0,
    rsi             REAL,
    macd_hist       REAL,
    bb_pband        REAL,
    rs_vs_sp500     REAL,
    earnings_blackout INTEGER DEFAULT 0,
    sector          TEXT    DEFAULT '',
    action          TEXT,
    strategy_type         TEXT    DEFAULT 'SAFE',
    catalyst_score  INTEGER DEFAULT 0,
    momentum_score  REAL    DEFAULT 0,
    reject_gate     TEXT    DEFAULT '',
    timestamp       TEXT    NOT NULL
);
"""

# Step 3: create indexes (run AFTER migrations so new columns exist)
_SCHEMA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_trades_side   ON trades(side)",
    "CREATE INDEX IF NOT EXISTS idx_trades_sector ON trades(sector)",
    "CREATE INDEX IF NOT EXISTS idx_scan_log_ts   ON scan_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_type)",
]

# Columns to add to existing databases (idempotent migration)
_MIGRATIONS = [
    "ALTER TABLE trades ADD COLUMN sector TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN confidence INTEGER DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN price_target REAL",
    "ALTER TABLE trades ADD COLUMN price_target_timeframe TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN trailing_stop_high REAL",
    "ALTER TABLE trades ADD COLUMN partial_sold INTEGER DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN sell_type TEXT DEFAULT ''",
    "ALTER TABLE scan_log ADD COLUMN confidence INTEGER DEFAULT 0",
    "ALTER TABLE scan_log ADD COLUMN rs_vs_sp500 REAL",
    "ALTER TABLE scan_log ADD COLUMN earnings_blackout INTEGER DEFAULT 0",
    "ALTER TABLE scan_log ADD COLUMN sector TEXT DEFAULT ''",
    # v3 migrations
    "ALTER TABLE trades ADD COLUMN strategy_type TEXT DEFAULT 'SAFE'",
    "ALTER TABLE trades ADD COLUMN catalyst_score INTEGER DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN catalyst_type TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN position_size_pct REAL DEFAULT 0.0",
    "ALTER TABLE trades ADD COLUMN tp_tier INTEGER DEFAULT 0",
    "ALTER TABLE scan_log ADD COLUMN strategy_type TEXT DEFAULT 'SAFE'",
    "ALTER TABLE scan_log ADD COLUMN catalyst_score INTEGER DEFAULT 0",
    "ALTER TABLE scan_log ADD COLUMN momentum_score REAL DEFAULT 0",
    # v4 migrations
    "ALTER TABLE scan_log ADD COLUMN reject_gate TEXT DEFAULT ''",
]


class TradeDB:
    """Manages the SQLite database for trades and scan history."""

    def __init__(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            # 1. Create tables (IF NOT EXISTS – safe on existing DBs)
            conn.executescript(_SCHEMA_TABLES)
            # 2. Add any missing columns to existing tables (idempotent)
            for sql in _MIGRATIONS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already exists
            # 3. Create indexes – now safe because all columns exist
            for sql in _SCHEMA_INDEXES:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
            conn.commit()
            log.info("Database initialised at %s", self._path)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── trades ───────────────────────────────────────────────

    def insert_trade(self, t: TradeRecord) -> int:
        sql = """
        INSERT INTO trades
            (ticker, side, quantity, price, value,
             sentiment, sentiment_score, rsi, bb_pband,
             reason, dry_run, pnl, timestamp,
             sector, confidence, price_target, price_target_timeframe,
             trailing_stop_high, partial_sold, sell_type,
             strategy_type, catalyst_score, catalyst_type,
             position_size_pct, tp_tier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            cur = conn.execute(sql, (
                t.ticker, t.side, t.quantity, t.price, t.value,
                t.sentiment, t.sentiment_score, t.rsi, t.bb_pband,
                t.reason, int(t.dry_run), t.pnl,
                t.timestamp or datetime.utcnow().isoformat(),
                t.sector, t.confidence, t.price_target, t.price_target_timeframe,
                t.trailing_stop_high, int(t.partial_sold), t.sell_type,
                t.strategy_type, t.catalyst_score, t.catalyst_type,
                t.position_size_pct, t.tp_tier,
            ))
            trade_id = cur.lastrowid
            log.info("Recorded %s trade #%d: %s qty=%.4f @%.2f", t.side, trade_id, t.ticker, t.quantity, t.price)
            return trade_id  # type: ignore[return-value]

    def get_open_buys(self) -> list[dict]:
        """Return BUY trades that don't have a matching full SELL yet."""
        sql = """
        SELECT b.*
        FROM trades b
        WHERE b.side = 'BUY'
          AND b.ticker NOT IN (
              SELECT s.ticker FROM trades s
              WHERE s.side = 'SELL' AND s.timestamp > b.timestamp
          )
        ORDER BY b.timestamp DESC
        """
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_open_buy_for_ticker(self, ticker: str) -> dict | None:
        sql = """
        SELECT b.*
        FROM trades b
        WHERE b.side = 'BUY' AND b.ticker = ?
          AND NOT EXISTS (
              SELECT 1 FROM trades s
              WHERE s.side = 'SELL' AND s.ticker = b.ticker AND s.timestamp > b.timestamp
          )
        ORDER BY b.timestamp DESC LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(sql, (ticker,)).fetchone()
            return dict(row) if row else None

    def update_trailing_stop(self, trade_id: int, new_high: float) -> None:
        """Update the trailing stop high-water mark for a position."""
        sql = "UPDATE trades SET trailing_stop_high = ? WHERE id = ?"
        with self._conn() as conn:
            conn.execute(sql, (new_high, trade_id))

    def mark_partial_sold(self, trade_id: int) -> None:
        """Mark a BUY trade as having had a partial profit take."""
        sql = "UPDATE trades SET partial_sold = 1 WHERE id = ?"
        with self._conn() as conn:
            conn.execute(sql, (trade_id,))

    def update_tp_tier(self, trade_id: int, tp_tier: int) -> None:
        """Update the take-profit tier reached for an aggressive position."""
        sql = "UPDATE trades SET tp_tier = ? WHERE id = ?"
        with self._conn() as conn:
            conn.execute(sql, (tp_tier, trade_id))

    def count_open_positions(self, strategy_type: str | None = None) -> int:
        """Count current open positions, optionally filtered by strategy."""
        buys = self.get_open_buys()
        if strategy_type:
            return len([b for b in buys if b.get("strategy_type", "SAFE") == strategy_type])
        return len(buys)

    def get_all_trades(self, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    def get_pnl_summary(self) -> dict:
        """Return total realised P&L and trade counts."""
        sql = """
        SELECT
            COUNT(*)                          AS total_trades,
            SUM(CASE WHEN side='BUY'  THEN 1 ELSE 0 END) AS buys,
            SUM(CASE WHEN side IN ('SELL','PARTIAL_SELL') THEN 1 ELSE 0 END) AS sells,
            COALESCE(SUM(CASE WHEN side IN ('SELL','PARTIAL_SELL') THEN pnl ELSE 0 END), 0) AS total_pnl,
            COALESCE(SUM(CASE WHEN side IN ('SELL','PARTIAL_SELL') AND pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
            COALESCE(SUM(CASE WHEN side IN ('SELL','PARTIAL_SELL') AND pnl <= 0 THEN 1 ELSE 0 END), 0) AS losses
        FROM trades
        """
        with self._conn() as conn:
            row = conn.execute(sql).fetchone()
            return dict(row) if row else {}

    # ── scan log ─────────────────────────────────────────────

    def log_scan(
        self,
        ticker: str,
        sentiment: str,
        sentiment_score: float,
        rsi: float,
        macd_hist: float,
        bb_pband: float,
        action: str,
        confidence: int = 0,
        rs_vs_sp500: float = 0.0,
        earnings_blackout: bool = False,
        sector: str = "",
        strategy_type: str = "SAFE",
        catalyst_score: int = 0,
        momentum_score: float = 0.0,
        reject_gate: str = "",
    ) -> None:
        sql = """
        INSERT INTO scan_log
            (ticker, sentiment, sentiment_score, confidence, rsi, macd_hist,
             bb_pband, rs_vs_sp500, earnings_blackout, sector, action,
             strategy_type, catalyst_score, momentum_score, reject_gate, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as conn:
            conn.execute(sql, (
                ticker, sentiment, sentiment_score, confidence,
                rsi, macd_hist, bb_pband, rs_vs_sp500,
                int(earnings_blackout), sector, action,
                strategy_type, catalyst_score, momentum_score,
                reject_gate, datetime.utcnow().isoformat(),
            ))

    def get_rejection_stats(self, days: int = 30) -> list[dict]:
        """Return rejection gate counts from scan_log for the last N days."""
        sql = """
        SELECT reject_gate, COUNT(*) AS count, COUNT(DISTINCT ticker) AS unique_tickers
        FROM scan_log
        WHERE reject_gate != '' AND reject_gate != 'BUY'
          AND timestamp >= datetime('now', ?)
        GROUP BY reject_gate
        ORDER BY count DESC
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (f"-{days} days",)).fetchall()
            return [dict(r) for r in rows]

    def get_dry_run_summary(self) -> dict:
        """Return P&L summary for dry-run trades only."""
        sql = """
        SELECT
            COUNT(CASE WHEN side='BUY'  THEN 1 END)                               AS signals,
            COUNT(CASE WHEN side='SELL' THEN 1 END)                               AS closed,
            COALESCE(SUM (CASE WHEN side='SELL' THEN pnl ELSE 0 END), 0)          AS total_pnl,
            COALESCE(SUM (CASE WHEN side='SELL' AND pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
            COALESCE(SUM (CASE WHEN side='SELL' AND pnl <= 0 THEN 1 ELSE 0 END), 0) AS losses,
            COALESCE(AVG (CASE WHEN side='SELL' THEN pnl END), 0)                 AS avg_pnl,
            COALESCE(AVG (CASE WHEN side='SELL' AND pnl > 0  THEN pnl END), 0)    AS avg_win,
            COALESCE(AVG (CASE WHEN side='SELL' AND pnl <= 0 THEN pnl END), 0)    AS avg_loss
        FROM trades
        WHERE dry_run = 1
        """
        with self._conn() as conn:
            row = conn.execute(sql).fetchone()
            return dict(row) if row else {}

    def get_recent_scans(self, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM scan_log ORDER BY timestamp DESC LIMIT ?"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]
