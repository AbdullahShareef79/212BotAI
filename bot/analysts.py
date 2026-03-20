"""Analyst ratings via yfinance.

Uses ticker.recommendations_summary (when available) which provides
pre-aggregated strongBuy/buy/hold/sell/strongSell counts per period.

Scoring formula (as specified):
  analyst_score = (strongBuy×2 + buy - sell - strongSell×2) / total
  Range: -1.0 (unanimous strong sell) … +1.0 (unanimous strong buy)

Falls back to ticker.recommendations keyword parsing if summary
is unavailable (older yfinance versions).

Also checks the last 7 days for recent upgrades.

Consensus buckets:
  score ≥  0.5  → STRONG_BUY
  score ≥  0.1  → BUY
  score ≤ -0.5  → STRONG_SELL
  score ≤ -0.1  → SELL
  else          → HOLD

Cached per ticker for 6 hours. Requires yfinance (already installed).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "AnalystResult"]] = {}
_CACHE_TTL = 21_600  # 6 hours

# Grade-string keyword maps (fallback path)
_BUY_KW       = {"buy", "outperform", "overweight", "upgrade", "positive", "add"}
_STRONG_BUY_KW = {"strong buy"}
_SELL_KW      = {"sell", "underperform", "underweight", "downgrade", "negative", "reduce"}
_STRONG_SELL_KW = {"strong sell"}


def _classify_grade(grade: str) -> str:
    g = grade.lower().strip()
    if any(k in g for k in _STRONG_BUY_KW):  return "STRONG_BUY"
    if any(k in g for k in _BUY_KW):          return "BUY"
    if any(k in g for k in _STRONG_SELL_KW): return "STRONG_SELL"
    if any(k in g for k in _SELL_KW):         return "SELL"
    return "HOLD"


@dataclass
class AnalystResult:
    ticker: str
    strong_buy: int = 0
    buy_count: int = 0
    hold_count: int = 0
    sell_count: int = 0
    strong_sell: int = 0
    analyst_score: float = 0.0    # -1.0 to +1.0
    consensus: str = "HOLD"        # STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL
    recent_upgrades: int = 0       # upgrades in last 7 days
    error: str = ""


def _score_to_consensus(score: float) -> str:
    if score >= 0.5:  return "STRONG_BUY"
    if score >= 0.1:  return "BUY"
    if score <= -0.5: return "STRONG_SELL"
    if score <= -0.1: return "SELL"
    return "HOLD"


def get_analyst_ratings(ticker: str) -> AnalystResult:
    """Fetch analyst ratings and compute weighted consensus score.

    Formula: (strongBuy×2 + buy - sell - strongSell×2) / total
    Returns a neutral AnalystResult on any error — never raises.
    Cached per ticker for 6 hours.
    """
    now = time.time()

    if ticker in _CACHE:
        ts, cached = _CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return cached

    try:
        import yfinance as yf  # type: ignore[import]

        t = yf.Ticker(ticker)

        # ── Primary: recommendations_summary (pre-aggregated counts) ──────────
        summary = getattr(t, "recommendations_summary", None)
        if summary is not None and not summary.empty:
            # Keep only the most recent period row
            row = summary.iloc[0]
            strong_buy  = int(row.get("strongBuy",  0))
            buy_raw     = int(row.get("buy",        0))
            hold_raw    = int(row.get("hold",       0))
            sell_raw    = int(row.get("sell",       0))
            strong_sell = int(row.get("strongSell", 0))
            total = strong_buy + buy_raw + hold_raw + sell_raw + strong_sell
            if total > 0:
                analyst_score = round(
                    (strong_buy * 2 + buy_raw - sell_raw - strong_sell * 2) / total, 3
                )
                # Count recent upgrades from the full recommendations list
                recent_upgrades = 0
                recs = getattr(t, "recommendations", None)
                if recs is not None and not recs.empty:
                    idx = recs.index
                    if hasattr(idx, "tz") and idx.tz is not None:
                        recs = recs.copy()
                        recs.index = idx.tz_convert(None)
                    cutoff_7 = datetime.utcnow() - timedelta(days=7)
                    grade_col = next(
                        (c for c in ("To Grade", "toGrade") if c in recs.columns), None
                    )
                    if grade_col:
                        for ts_idx, r_row in recs[recs.index >= cutoff_7].iterrows():
                            if _classify_grade(str(r_row.get(grade_col, ""))) in ("BUY", "STRONG_BUY"):
                                recent_upgrades += 1

                result = AnalystResult(
                    ticker=ticker,
                    strong_buy=strong_buy,
                    buy_count=buy_raw,
                    hold_count=hold_raw,
                    sell_count=sell_raw,
                    strong_sell=strong_sell,
                    analyst_score=analyst_score,
                    consensus=_score_to_consensus(analyst_score),
                    recent_upgrades=recent_upgrades,
                )
                _CACHE[ticker] = (now, result)
                log.info(
                    "Analysts %s (summary): score=%.2f consensus=%s "
                    "(SB=%d B=%d H=%d S=%d SS=%d upgrades=%d)",
                    ticker, analyst_score, result.consensus,
                    strong_buy, buy_raw, hold_raw, sell_raw, strong_sell, recent_upgrades,
                )
                return result

        # ── Fallback: parse ticker.recommendations rows manually ──────────────
        recs = getattr(t, "recommendations", None)
        if recs is None or (hasattr(recs, "empty") and recs.empty):
            result = AnalystResult(ticker=ticker, error="No recommendations data")
            _CACHE[ticker] = (now, result)
            return result

        idx = recs.index
        if hasattr(idx, "tz") and idx.tz is not None:
            recs = recs.copy()
            recs.index = idx.tz_convert(None)

        cutoff_30 = datetime.utcnow() - timedelta(days=30)
        cutoff_7  = datetime.utcnow() - timedelta(days=7)
        recent = recs[recs.index >= cutoff_30]

        if recent.empty:
            result = AnalystResult(ticker=ticker, error="No recommendations in last 30 days")
            _CACHE[ticker] = (now, result)
            return result

        grade_col = next(
            (c for c in ("To Grade", "toGrade", "Action", "action") if c in recent.columns),
            recent.columns[0],
        )

        strong_buy = buy_raw = hold_raw = sell_raw = strong_sell = recent_upgrades = 0

        for ts_idx, row in recent.iterrows():
            cat = _classify_grade(str(row.get(grade_col, "")))
            if cat == "STRONG_BUY":
                strong_buy += 1
                if ts_idx >= cutoff_7:
                    recent_upgrades += 1
            elif cat == "BUY":
                buy_raw += 1
                if ts_idx >= cutoff_7:
                    recent_upgrades += 1
            elif cat == "STRONG_SELL":
                strong_sell += 1
            elif cat == "SELL":
                sell_raw += 1
            else:
                hold_raw += 1

        total = strong_buy + buy_raw + hold_raw + sell_raw + strong_sell
        analyst_score = round(
            (strong_buy * 2 + buy_raw - sell_raw - strong_sell * 2) / total, 3
        ) if total > 0 else 0.0

        result = AnalystResult(
            ticker=ticker,
            strong_buy=strong_buy,
            buy_count=buy_raw,
            hold_count=hold_raw,
            sell_count=sell_raw,
            strong_sell=strong_sell,
            analyst_score=analyst_score,
            consensus=_score_to_consensus(analyst_score),
            recent_upgrades=recent_upgrades,
        )
        _CACHE[ticker] = (now, result)
        log.info(
            "Analysts %s (fallback): score=%.2f consensus=%s upgrades=%d",
            ticker, analyst_score, result.consensus, recent_upgrades,
        )
        return result

    except Exception as exc:
        log.warning("Analyst fetch failed for %s: %s", ticker, exc)
        result = AnalystResult(ticker=ticker, error=str(exc))
        _CACHE[ticker] = (now, result)
        return result
