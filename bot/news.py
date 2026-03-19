"""Fetch recent headlines for a stock ticker from NewsAPI (free tier)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)

_BASE = "https://newsapi.org/v2/everything"


class NewsClient:
    """Fetches headlines from NewsAPI for a given company / ticker."""

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._client = httpx.Client(timeout=20)

    def fetch_headlines(
        self,
        query: str,
        days_back: int = 3,
        max_articles: int = 10,
        language: str = "en",
    ) -> list[dict[str, Any]]:
        """Return a list of article dicts (title, description, source, url, publishedAt)."""
        from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "relevancy",
            "pageSize": max_articles,
            "language": language,
            "apiKey": self._key,
        }
        try:
            resp = self._client.get(_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
            articles = data.get("articles", [])
            log.info("NewsAPI returned %d articles for '%s'", len(articles), query)
            return [
                {
                    "title": a.get("title", ""),
                    "description": a.get("description", ""),
                    "source": (a.get("source") or {}).get("name", ""),
                    "url": a.get("url", ""),
                    "publishedAt": a.get("publishedAt", ""),
                }
                for a in articles
                if a.get("title")
            ]
        except httpx.HTTPStatusError as exc:
            log.error("NewsAPI HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
            return []
        except Exception as exc:
            log.error("NewsAPI error: %s", exc)
            return []

    def close(self) -> None:
        self._client.close()
