"""Centralised configuration – loads .env and exposes typed settings."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from dotenv import load_dotenv

# ── Load .env from project root ────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")


def _env(key: str, default: str | None = None, required: bool = False) -> str:
    val = os.getenv(key, default)
    if required and not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val or ""


@dataclass(frozen=True)
class Config:
    # Trading 212
    trading212_api_key: str = field(default_factory=lambda: _env("TRADING212_API_KEY", required=True))
    trading212_env: str = field(default_factory=lambda: _env("TRADING212_ENV", "practice"))

    # OpenAI
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", required=True))

    # NewsAPI
    newsapi_key: str = field(default_factory=lambda: _env("NEWSAPI_KEY", required=True))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", ""))

    # Strategy
    dry_run: bool = field(default_factory=lambda: _env("DRY_RUN", "true").lower() in ("true", "1", "yes"))
    order_size_eur: float = field(default_factory=lambda: float(_env("ORDER_SIZE_EUR", "100")))
    take_profit_pct: float = field(default_factory=lambda: float(_env("TAKE_PROFIT_PCT", "8.0")))
    stop_loss_pct: float = field(default_factory=lambda: float(_env("STOP_LOSS_PCT", "2.0")))
    partial_tp_pct: float = field(default_factory=lambda: float(_env("PARTIAL_TP_PCT", "3.0")))
    min_confidence: int = field(default_factory=lambda: int(_env("MIN_CONFIDENCE", "75")))

    # Portfolio limits
    max_positions: int = field(default_factory=lambda: int(_env("MAX_POSITIONS", "10")))
    max_sector_pct: float = field(default_factory=lambda: float(_env("MAX_SECTOR_PCT", "20.0")))
    earnings_blackout_days: int = field(default_factory=lambda: int(_env("EARNINGS_BLACKOUT_DAYS", "3")))

    # Scheduler
    scan_hour: int = field(default_factory=lambda: int(_env("SCAN_HOUR", "9")))
    scan_minute: int = field(default_factory=lambda: int(_env("SCAN_MINUTE", "0")))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Europe/Berlin"))
    rebalance_day: str = field(default_factory=lambda: _env("REBALANCE_DAY", "friday"))

    # Paths
    db_path: str = field(default_factory=lambda: str(_ROOT / "data" / "trades.db"))

    @property
    def trading212_base_url(self) -> str:
        if self.trading212_env == "live":
            return "https://live.trading212.com"
        return "https://demo.trading212.com"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


# Singleton
cfg = Config()
