"""Fear & Greed Index via alternative.me (free, no API key).

Endpoint: https://api.alternative.me/fng/
Cached globally for 1 hour — the index updates once per day so
more frequent fetching just wastes bandwidth.

Interpretation:
  0–24   Extreme Fear  → contrarian buying opportunity
  25–44  Fear          → cautious
  45–54  Neutral
  55–74  Greed
  75–100 Extreme Greed → avoid new longs; market may be overextended
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

# ── Global 1-hour cache ───────────────────────────────────────
_CACHE: tuple[float, "FearGreedResult"] | None = None
_CACHE_TTL = 3600  # seconds

_API_URL = "https://api.alternative.me/fng/?limit=1"


@dataclass
class FearGreedResult:
    value: int = 50               # 0-100
    label: str = "Neutral"        # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"
    signal: str = "NEUTRAL"       # EXTREME_FEAR_BUY / FEAR / NEUTRAL / GREED / EXTREME_GREED_AVOID
    is_buying_opportunity: bool = False   # value <= 25 — extreme fear = contrarian buy
    avoid_buying: bool = False            # value >= 75 — extreme greed = caution
    error: str = ""


def get_fear_greed() -> FearGreedResult:
    """Return the current Fear & Greed index. Cached for 1 hour. Never raises."""
    global _CACHE
    now = time.time()

    if _CACHE and (now - _CACHE[0]) < _CACHE_TTL:
        return _CACHE[1]

    try:
        resp = requests.get(_API_URL, timeout=10)
        resp.raise_for_status()
        entry = resp.json()["data"][0]

        value = int(entry["value"])
        label = str(entry["value_classification"])

        if value <= 25:
            signal = "EXTREME FEAR — BUY OPPORTUNITY"
        elif value <= 45:
            signal = "FEAR — Proceed with caution"
        elif value <= 55:
            signal = "NEUTRAL"
        elif value <= 75:
            signal = "GREED"
        else:
            signal = "EXTREME GREED — AVOID NEW LONGS"

        result = FearGreedResult(
            value=value,
            label=label,
            signal=signal,
            is_buying_opportunity=value <= 25,
            avoid_buying=value >= 75,
        )
        _CACHE = (now, result)
        log.info("Fear & Greed: %d (%s)", value, label)
        return result

    except Exception as exc:
        log.warning("Fear & Greed fetch failed: %s", exc)
        result = FearGreedResult(error=str(exc))
        # Don't cache errors — retry on next call
        return result
