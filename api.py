"""StockBot AI v3 — Web Dashboard API

Exposes bot state, trades, and control endpoints for stockbot_dashboard.html.

Run:
    python api.py
    (then open stockbot_dashboard.html in your browser)

Requires:
    pip install flask flask-cors
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import datetime

ROOT     = pathlib.Path(__file__).resolve().parent
DB_PATH  = ROOT / "data" / "trades.db"
ENV_PATH = ROOT / ".env"

try:
    from flask import Flask, jsonify, request
    from flask_cors import CORS
except ImportError:
    print("Missing dependencies. Run:  pip install flask flask-cors")
    raise

app = Flask(__name__, static_folder=str(ROOT))
CORS(app)


@app.route("/")
def dashboard():
    return (ROOT / "stockbot_dashboard.html").read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _q(sql: str, params: tuple = ()) -> list[dict]:
    if not DB_PATH.exists():
        return []
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _open_buys() -> list[dict]:
    return _q("""
        SELECT b.* FROM trades b
        WHERE b.side = 'BUY'
          AND b.ticker NOT IN (
              SELECT s.ticker FROM trades s
              WHERE s.side = 'SELL' AND s.timestamp > b.timestamp
          )
        ORDER BY b.timestamp DESC
    """)


# ── GET /api/status ───────────────────────────────────────────────────────────

@app.route("/api/status")
def status():
    try:
        from dotenv import dotenv_values
        env = dotenv_values(ENV_PATH)
        dry = env.get("DRY_RUN", "true").lower() in ("true", "1", "yes")
        mode = "DRY-RUN" if dry else "LIVE"

        last_scan_rows = _q("SELECT MAX(timestamp) as ts FROM scan_log")
        last_scan = last_scan_rows[0]["ts"] if last_scan_rows else None

        open_count = len(_open_buys())

        return jsonify({
            "mode":            mode,
            "t212_env":        env.get("TRADING212_ENV", "practice"),
            "last_scan":       last_scan,
            "open_positions":  open_count,
            "max_positions":   env.get("MAX_POSITIONS", "10"),
            "server_time":     datetime.utcnow().isoformat() + "Z",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── GET /api/positions ────────────────────────────────────────────────────────

@app.route("/api/positions")
def positions():
    try:
        rows = _open_buys()

        # Enrich with live price (yfinance fast_info — no API quota used)
        try:
            import yfinance as yf
            for row in rows:
                try:
                    price = yf.Ticker(row["ticker"]).fast_info.last_price
                    if price and price > 0:
                        entry = row.get("price") or 0
                        qty   = row.get("quantity") or 0
                        row["current_price"] = round(float(price), 4)
                        row["pnl_pct"] = round(((price - entry) / entry) * 100, 2) if entry else None
                        row["pnl_eur"] = round((price - entry) * qty, 2) if entry else None
                    else:
                        row["current_price"] = row["pnl_pct"] = row["pnl_eur"] = None
                except Exception:
                    row["current_price"] = row["pnl_pct"] = row["pnl_eur"] = None
        except ImportError:
            pass

        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── GET /api/trades ───────────────────────────────────────────────────────────

@app.route("/api/trades")
def trades():
    try:
        limit  = int(request.args.get("limit", 100))
        side   = request.args.get("side")
        ticker = request.args.get("ticker")

        sql, params = "SELECT * FROM trades WHERE 1=1", []
        if side:
            sql += " AND side = ?"; params.append(side)
        if ticker:
            sql += " AND upper(ticker) = ?"; params.append(ticker.upper())
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        return jsonify(_q(sql, tuple(params)))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── GET /api/metrics ──────────────────────────────────────────────────────────

@app.route("/api/metrics")
def metrics():
    try:
        sells = _q("SELECT pnl, confidence FROM trades WHERE side = 'SELL'")
        total_sells = len(sells)
        pnl_vals = [r["pnl"] or 0 for r in sells]
        total_pnl = sum(pnl_vals)
        wins   = [p for p in pnl_vals if p > 0]
        losses = [p for p in pnl_vals if p <= 0]

        total_buys = _q("SELECT COUNT(*) as c FROM trades WHERE side = 'BUY'")[0]["c"]
        open_count = len(_open_buys())

        return jsonify({
            "total_pnl":      round(total_pnl, 2),
            "win_rate":       round(len(wins) / total_sells * 100, 1) if total_sells else 0,
            "wins":           len(wins),
            "losses":         len(losses),
            "avg_win":        round(sum(wins)  / len(wins),   2) if wins   else 0,
            "avg_loss":       round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor":  round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else 0,
            "total_trades":   total_buys + total_sells,
            "total_buys":     total_buys,
            "total_sells":    total_sells,
            "open_positions": open_count,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── GET /api/scan-results ─────────────────────────────────────────────────────
# SELECT * automatically includes all v4 intelligence columns:
#   reddit_score, fear_greed, insider_signal, put_call_ratio,
#   analyst_score, data_sources_used

@app.route("/api/scan-results")
def scan_results():
    try:
        limit  = int(request.args.get("limit", 200))
        ticker = request.args.get("ticker")
        sql, params = "SELECT * FROM scan_log WHERE 1=1", []
        if ticker:
            sql += " AND upper(ticker) = ?"; params.append(ticker.upper())
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = _q(sql, tuple(params))
        return jsonify(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── GET /api/market-intelligence ─────────────────────────────────────────────

@app.route("/api/market-intelligence")
def market_intelligence():
    """Aggregate view of all intelligence sources for the Intelligence tab."""
    try:
        result: dict = {}

        # Fear & Greed
        try:
            from bot.market_sentiment import get_fear_greed
            fg = get_fear_greed()
            result["fear_greed"] = {
                "value":                fg.value,
                "label":                fg.label,
                "signal":               fg.signal,
                "is_buying_opportunity": fg.is_buying_opportunity,
                "avoid_buying":         fg.avoid_buying,
            }
        except Exception as exc:
            result["fear_greed"] = {"error": str(exc)}

        # Most recent scan log rows (latest scan cycle)
        scan_rows = _q("""
            SELECT ticker, reddit_score, insider_signal, put_call_ratio,
                   analyst_score, timestamp as scanned_at
            FROM scan_log
            WHERE timestamp >= datetime('now', '-2 hours')
            ORDER BY reddit_score DESC
            LIMIT 50
        """)

        # Top Reddit mentions from last scan
        reddit_rows = [
            {"ticker": r["ticker"], "reddit_score": r["reddit_score"],
             "scanned_at": r["scanned_at"]}
            for r in scan_rows
            if r.get("reddit_score") is not None and r["reddit_score"] > 0
        ]
        reddit_rows.sort(key=lambda x: x["reddit_score"] or 0, reverse=True)
        result["top_reddit_mentions"] = reddit_rows[:10]

        # Insider BUY signals from last scan
        insider_buys = [
            {
                "ticker":            r["ticker"],
                "insider_signal":    r["insider_signal"],
                "insider_buy_count": None,   # stored as signal text only
                "insider_sell_count": None,
                "scanned_at":        r["scanned_at"],
            }
            for r in scan_rows
            if r.get("insider_signal") == "BUY"
        ]
        result["insider_buy_signals"] = insider_buys

        # Summary signals across last scan
        insider_signals = [r["insider_signal"] for r in scan_rows if r.get("insider_signal")]
        buy_count  = insider_signals.count("BUY")
        sell_count = insider_signals.count("SELL")
        if buy_count > sell_count:
            result["insider_summary"] = f"NET BUY ({buy_count} tickers)"
        elif sell_count > buy_count:
            result["insider_summary"] = f"NET SELL ({sell_count} tickers)"
        else:
            result["insider_summary"] = "NEUTRAL"

        pcs = [r["put_call_ratio"] for r in scan_rows if r.get("put_call_ratio") is not None]
        result["avg_put_call"] = round(sum(pcs) / len(pcs), 3) if pcs else None

        analyst_scores = [r["analyst_score"] for r in scan_rows if r.get("analyst_score") is not None]
        if analyst_scores:
            avg_a = sum(analyst_scores) / len(analyst_scores)
            result["analyst_consensus"] = (
                "STRONG_BUY" if avg_a >= 0.5 else
                "BUY"        if avg_a >= 0.1 else
                "STRONG_SELL" if avg_a <= -0.5 else
                "SELL"       if avg_a <= -0.1 else "HOLD"
            )
        else:
            result["analyst_consensus"] = None

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── GET /api/settings ─────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    try:
        from dotenv import dotenv_values
        env = dotenv_values(ENV_PATH)
        # Strip secrets from the response
        safe = {
            k: v for k, v in env.items()
            if not any(x in k for x in ("KEY", "SECRET", "TOKEN", "PASSWORD"))
        }
        return jsonify(safe)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── POST /api/settings ────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["POST"])
def save_settings():
    try:
        data = request.json or {}
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        updated: set[str] = set()
        new_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in data:
                new_lines.append(f"{key}={data[key]}")
                updated.add(key)
            else:
                new_lines.append(line)

        # Append any keys not already present
        for k, v in data.items():
            if k not in updated:
                new_lines.append(f"{k}={v}")

        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return jsonify({"ok": True, "updated": sorted(updated)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── POST /api/scan ────────────────────────────────────────────────────────────

@app.route("/api/scan", methods=["POST"])
def trigger_scan():
    try:
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "main.py"), "--scan-now"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({"ok": True, "pid": proc.pid})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🤖 StockBot Dashboard API  →  http://localhost:5000")
    print("   Open stockbot_dashboard.html in your browser")
    app.run(host="0.0.0.0", port=5000, debug=False)
