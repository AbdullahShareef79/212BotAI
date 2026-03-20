"""Reddit sentiment scanner using Reddit's public JSON API.

No API key or PRAW required. Uses Reddit's public *.json endpoints:
  https://www.reddit.com/r/<sub>/hot.json?limit=100

Scans r/wallstreetbets, r/stocks, r/investing for posts mentioning
a ticker in the last 24 hours, then scores sentiment on those titles
using a keyword heuristic.

Scoring (0–100):
  mention_component  = min(mentions / 30, 1.0) × 80    → 0-80
  sentiment_boost    = ((avg_sentiment + 1) / 2) × 20  → 0-20
  reddit_score       = mention_component + sentiment_boost

Cached per ticker for 1 hour to avoid hammering the public endpoint.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

# ── 1-hour in-memory cache keyed by ticker ───────────────────
_CACHE: dict[str, tuple[float, "RedditResult"]] = {}
_CACHE_TTL = 3600  # seconds

_HEADERS = {"User-Agent": "StockBotAI/1.0 personal bot"}
_SUBREDDITS = ("wallstreetbets", "stocks", "investing")
_CUTOFF_SECS = 86_400  # 24 hours


@dataclass
class RedditResult:
    ticker: str
    mention_count: int = 0
    sentiment_avg: float = 0.0   # -1.0 … +1.0
    reddit_score: float = 0.0    # 0-100
    top_comments: list[str] = field(default_factory=list)  # matching post titles
    error: str = ""


# ── Lightweight keyword sentiment (no ML dependency) ─────────

_BULLISH_KW = [
    "bullish", "moon", "buy", "calls", "yolo", "long", "pump",
    "breakout", "upside", "strong buy", "🚀", "💎", "🙌",
    "squeeze", "rally", "rip", "mooning", "undervalued", "beat",
    "earnings beat", "upgrade", "outperform",
]
_BEARISH_KW = [
    "bearish", "puts", "short", "dump", "crash", "sell", "scam",
    "red", "down", "dropping", "miss", "loss", "fade", "trap",
    "overvalued", "downgrade", "underperform", "garbage",
    "layoffs", "fraud", "warning", "cut",
]


def _keyword_sentiment(text: str) -> float:
    """Return polarity in [-1.0, +1.0] using keyword counts."""
    t = text.lower()
    bull = sum(1 for w in _BULLISH_KW if w in t)
    bear = sum(1 for w in _BEARISH_KW if w in t)
    total = bull + bear
    return 0.0 if total == 0 else (bull - bear) / total


def _ticker_mentioned(ticker: str, text: str) -> bool:
    """True if *ticker* appears as a whole word (case-insensitive)."""
    return bool(re.search(rf"\b{re.escape(ticker)}\b", text, re.IGNORECASE))


# ── Fetch one subreddit's hot posts ──────────────────────────

def _fetch_posts(subreddit: str) -> list[dict]:
    """Return raw post data dicts from /r/<subreddit>/hot.json.

    Returns an empty list on any network / parsing error.
    """
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=100"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        children = resp.json()["data"]["children"]
        return [c["data"] for c in children]
    except Exception as exc:
        log.debug("Reddit fetch r/%s failed: %s", subreddit, exc)
        return []


# ── Public API ────────────────────────────────────────────────

def get_reddit_score(ticker: str) -> RedditResult:
    """Scan Reddit hot posts for *ticker* mentions in the last 24 hours.

    Uses only public JSON endpoints — no API key required.
    Returns a neutral RedditResult on any error; never raises.
    Cached for 1 hour per ticker.
    """
    now = time.time()

    # ── Cache hit ─────────────────────────────────────────────
    if ticker in _CACHE:
        ts, cached = _CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return cached

    cutoff = now - _CUTOFF_SECS
    mentions = 0
    sentiments: list[float] = []
    top_titles: list[str] = []

    for sub in _SUBREDDITS:
        posts = _fetch_posts(sub)
        for post in posts:
            # Filter to last 24 hours
            if post.get("created_utc", 0) < cutoff:
                continue

            title    = post.get("title", "")
            selftext = post.get("selftext", "")
            full     = f"{title} {selftext}"

            if not _ticker_mentioned(ticker, full):
                continue

            mentions += 1
            sentiments.append(_keyword_sentiment(full))

            if len(top_titles) < 3:
                top_titles.append(title[:200])

    sentiment_avg   = sum(sentiments) / len(sentiments) if sentiments else 0.0
    mention_score   = min(mentions / 30.0, 1.0) * 80           # 0-80
    sentiment_boost = ((sentiment_avg + 1.0) / 2.0) * 20       # 0-20
    reddit_score    = round(min(mention_score + sentiment_boost, 100.0), 1)

    result = RedditResult(
        ticker=ticker,
        mention_count=mentions,
        sentiment_avg=round(sentiment_avg, 3),
        reddit_score=reddit_score,
        top_comments=top_titles,
    )
    _CACHE[ticker] = (now, result)
    log.info(
        "Reddit %s: %d mentions | sentiment=%.2f | score=%.1f",
        ticker, mentions, sentiment_avg, reddit_score,
    )
    return result
