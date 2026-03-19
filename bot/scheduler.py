"""Scheduler – runs the strategy at market open every weekday."""

from __future__ import annotations

import logging
import time
from datetime import datetime

import pytz
import schedule

log = logging.getLogger(__name__)


def next_run_str(tz_name: str) -> str:
    """Human-readable string for the next scheduled job."""
    jobs = schedule.get_jobs()
    if not jobs:
        return "No jobs scheduled"
    nxt = schedule.idle_seconds()
    if nxt is None:
        return "—"
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    from datetime import timedelta

    target = now + timedelta(seconds=nxt)
    return target.strftime("%Y-%m-%d %H:%M %Z")


def is_weekday() -> bool:
    return datetime.utcnow().weekday() < 5  # Mon-Fri


def setup_schedule(hour: int, minute: int, tz_name: str, job_func) -> None:
    """Schedule *job_func* to run at HH:MM in the given timezone, weekdays only.

    ``schedule`` library works in local time, so we compute the UTC offset and
    schedule accordingly.
    """
    time_str = f"{hour:02d}:{minute:02d}"
    tz = pytz.timezone(tz_name)

    def _wrapper():
        if not is_weekday():
            log.info("Weekend – skipping scan")
            return
        log.info("⏰ Scheduled scan triggered at %s %s", time_str, tz_name)
        job_func()

    # schedule uses local-machine time; convert target tz → local
    # Simpler: just schedule in the target TZ string (schedule >=1.2 supports tz)
    schedule.every().day.at(time_str, tz_name).do(_wrapper)
    log.info("Scheduled daily scan at %s %s (weekdays)", time_str, tz_name)


def run_loop(on_tick=None) -> None:
    """Blocking loop that runs pending schedule jobs and calls *on_tick* each second."""
    log.info("Entering scheduler loop (Ctrl+C to stop)")
    try:
        while True:
            schedule.run_pending()
            if on_tick:
                on_tick()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Scheduler stopped by user")
