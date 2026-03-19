"""Earnings calendar checker – avoid buying near earnings dates.

Uses yfinance to fetch upcoming earnings dates and enforces a blackout
window of N days before earnings (default: 3 days).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import yfinance as yf

log = logging.getLogger(__name__)

_BLACKOUT_DAYS = 3


def get_next_earnings_date(ticker: str) -> datetime | None:
    """Return the next earnings date for *ticker*, or None if unavailable."""
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if cal is None or cal.empty if hasattr(cal, 'empty') else not cal:
            return None

        # yfinance returns calendar as a dict or DataFrame depending on version
        if isinstance(cal, dict):
            # Newer yfinance versions return dict with 'Earnings Date' key
            dates = cal.get("Earnings Date")
            if dates:
                if isinstance(dates, list):
                    # Return the earliest future date
                    now = datetime.now()
                    future = [d for d in dates if d > now]
                    return min(future) if future else None
                return dates if isinstance(dates, datetime) else None
        else:
            # DataFrame format: look for 'Earnings Date' row
            if "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"].iloc[0]
                if hasattr(val, "to_pydatetime"):
                    return val.to_pydatetime()
                return None

        # Fallback: try the .earnings_dates property
        if hasattr(stock, "earnings_dates") and stock.earnings_dates is not None:
            eds = stock.earnings_dates
            if not eds.empty:
                now = datetime.now()
                future_dates = eds.index[eds.index > now.strftime("%Y-%m-%d")]
                if len(future_dates) > 0:
                    return future_dates[0].to_pydatetime()

        return None
    except Exception as exc:
        log.debug("Could not fetch earnings date for %s: %s", ticker, exc)
        return None


def is_in_earnings_blackout(ticker: str, blackout_days: int = _BLACKOUT_DAYS) -> bool:
    """Return True if *ticker* has earnings within the next *blackout_days* days."""
    next_date = get_next_earnings_date(ticker)
    if next_date is None:
        return False  # No data → allow trading

    now = datetime.now()
    # Handle timezone-aware datetimes
    if next_date.tzinfo is not None:
        from datetime import timezone
        now = datetime.now(timezone.utc)

    days_until = (next_date - now).days

    if 0 <= days_until <= blackout_days:
        log.info(
            "⚠️ %s earnings in %d days (%s) – BLACKOUT",
            ticker, days_until, next_date.strftime("%Y-%m-%d"),
        )
        return True

    return False


def get_earnings_info(ticker: str) -> dict:
    """Return a dict with earnings-related info for display."""
    next_date = get_next_earnings_date(ticker)
    if next_date is None:
        return {"next_earnings": None, "days_until": None, "in_blackout": False}

    now = datetime.now()
    if next_date.tzinfo is not None:
        from datetime import timezone
        now = datetime.now(timezone.utc)

    days_until = (next_date - now).days
    return {
        "next_earnings": next_date.strftime("%Y-%m-%d"),
        "days_until": days_until,
        "in_blackout": 0 <= days_until <= _BLACKOUT_DAYS,
    }
