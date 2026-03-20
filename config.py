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
    trading212_api_secret: str = field(default_factory=lambda: _env("TRADING212_API_SECRET", ""))
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
    take_profit_pct: float = field(default_factory=lambda: float(_env("TAKE_PROFIT_PCT", "15.0")))
    stop_loss_pct: float = field(default_factory=lambda: float(_env("STOP_LOSS_PCT", "4.0")))
    partial_tp_pct: float = field(default_factory=lambda: float(_env("PARTIAL_TP_PCT", "5.0")))
    min_confidence: int = field(default_factory=lambda: int(_env("MIN_CONFIDENCE", "75")))
    min_sell_confidence: int = field(default_factory=lambda: int(_env("MIN_SELL_CONFIDENCE", "60")))

    # Portfolio limits
    max_positions: int = field(default_factory=lambda: int(_env("MAX_POSITIONS", "10")))
    max_sector_pct: float = field(default_factory=lambda: float(_env("MAX_SECTOR_PCT", "35.0")))
    min_hold_days: int = field(default_factory=lambda: int(_env("MIN_HOLD_DAYS", "5")))
    earnings_blackout_days: int = field(default_factory=lambda: int(_env("EARNINGS_BLACKOUT_DAYS", "3")))

    # ── Aggressive strategy (v3) ───────────────────────────
    agg_enabled: bool = field(default_factory=lambda: _env("AGG_ENABLED", "true").lower() in ("true", "1", "yes"))
    agg_stop_loss_pct: float = field(default_factory=lambda: float(_env("AGG_STOP_LOSS_PCT", "5.0")))
    agg_tp_tier1: float = field(default_factory=lambda: float(_env("AGG_TP_TIER1", "15.0")))
    agg_tp_tier2: float = field(default_factory=lambda: float(_env("AGG_TP_TIER2", "30.0")))
    agg_tp_tier3: float = field(default_factory=lambda: float(_env("AGG_TP_TIER3", "50.0")))
    agg_max_positions: int = field(default_factory=lambda: int(_env("AGG_MAX_POSITIONS", "5")))
    min_catalyst_score: int = field(default_factory=lambda: int(_env("MIN_CATALYST_SCORE", "7")))

    # Momentum scanner thresholds
    momentum_min_gain_pct: float = field(default_factory=lambda: float(_env("MOMENTUM_MIN_GAIN_PCT", "5.0")))
    momentum_vol_multiplier: float = field(default_factory=lambda: float(_env("MOMENTUM_VOL_MULTIPLIER", "3.0")))

    # Conviction-based position sizing (% of portfolio)
    agg_size_moderate_pct: float = field(default_factory=lambda: float(_env("AGG_SIZE_MODERATE_PCT", "5.0")))
    agg_size_high_pct: float = field(default_factory=lambda: float(_env("AGG_SIZE_HIGH_PCT", "15.0")))
    agg_size_max_pct: float = field(default_factory=lambda: float(_env("AGG_SIZE_MAX_PCT", "25.0")))

    # Scheduler
    scan_hour: int = field(default_factory=lambda: int(_env("SCAN_HOUR", "9")))
    scan_minute: int = field(default_factory=lambda: int(_env("SCAN_MINUTE", "0")))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Europe/Berlin"))
    rebalance_day: str = field(default_factory=lambda: _env("REBALANCE_DAY", "friday"))

    # ── Multi-source intelligence (v4) ────────────────────
    enable_reddit: bool = field(default_factory=lambda: _env("ENABLE_REDDIT", "true").lower() in ("true", "1", "yes"))
    enable_options_flow: bool = field(default_factory=lambda: _env("ENABLE_OPTIONS_FLOW", "true").lower() in ("true", "1", "yes"))
    enable_insider: bool = field(default_factory=lambda: _env("ENABLE_INSIDER", "true").lower() in ("true", "1", "yes"))
    enable_trends: bool = field(default_factory=lambda: _env("ENABLE_TRENDS", "false").lower() in ("true", "1", "yes"))

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
