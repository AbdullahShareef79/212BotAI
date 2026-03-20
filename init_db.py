"""Bootstrap an empty trades.db so the dashboard works before the bot first runs."""
import pathlib, sqlite3

ROOT = pathlib.Path(__file__).resolve().parent
DB   = ROOT / "data" / "trades.db"
DB.parent.mkdir(exist_ok=True)

conn = sqlite3.connect(str(DB))
conn.executescript("""
CREATE TABLE IF NOT EXISTS trades (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                TEXT,
    side                  TEXT,
    quantity              REAL,
    price                 REAL,
    value                 REAL,
    sentiment             TEXT,
    sentiment_score       REAL,
    rsi                   REAL,
    bb_pband              REAL,
    reason                TEXT,
    dry_run               INTEGER DEFAULT 1,
    pnl                   REAL,
    timestamp             TEXT,
    sector                TEXT DEFAULT '',
    confidence            INTEGER DEFAULT 0,
    price_target          REAL,
    price_target_timeframe TEXT DEFAULT '',
    trailing_stop_high    REAL,
    partial_sold          INTEGER DEFAULT 0,
    sell_type             TEXT DEFAULT '',
    strategy_type         TEXT DEFAULT 'SAFE',
    catalyst_score        INTEGER DEFAULT 0,
    catalyst_type         TEXT DEFAULT '',
    position_size_pct     REAL DEFAULT 0.0,
    tp_tier               INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT,
    sentiment         TEXT,
    sentiment_score   REAL,
    confidence        INTEGER DEFAULT 0,
    rsi               REAL,
    macd_hist         REAL,
    bb_pband          REAL,
    rs_vs_sp500       REAL,
    earnings_blackout INTEGER DEFAULT 0,
    sector            TEXT DEFAULT '',
    action            TEXT,
    strategy_type     TEXT DEFAULT 'SAFE',
    catalyst_score    INTEGER DEFAULT 0,
    momentum_score    REAL DEFAULT 0,
    reject_gate       TEXT DEFAULT '',
    timestamp         TEXT
);
""")
conn.commit()
conn.close()
print(f"DB ready at {DB}")
