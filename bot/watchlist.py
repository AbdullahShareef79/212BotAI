"""Two-tier watchlist management for hybrid safe/aggressive strategy.

Tier 1 (SAFE):       S&P 500 blue-chip stocks – stable, liquid, lower risk
Tier 2 (AGGRESSIVE): Small/mid-cap stocks $50M-$2B – explosive potential, higher risk

The watchlist is maintained here as the single source of truth.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ── Tier 1: S&P 500 Blue Chips (Safe Strategy) ─────────────
# These are the original 50 most-traded tickers on Trading 212.
TIER1_SAFE: list[str] = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "NVDA", "JPM",
    "V", "JNJ", "WMT", "PG", "UNH", "HD", "MA", "DIS", "BAC",
    "XOM", "ADBE", "CRM", "NFLX", "CSCO", "PFE", "INTC", "KO",
    "PEP", "ABT", "TMO", "MRK", "AVGO", "COST", "NKE", "CVX",
    "LLY", "ORCL", "ACN", "MCD", "MDT", "TXN", "QCOM", "AMD",
    "PYPL", "AMGN", "LOW", "IBM", "GE", "CAT", "BA", "SBUX", "UBER",
    # Energy (outperforming)
    "SLB", "EOG", "PSX", "MPC", "VLO",
    # Financials (stable)
    "GS", "MS", "BLK", "AXP", "SPGI",
    # Industrials momentum
    "RTX", "LMT", "NOC", "GD",
]

# ── Tier 2: Small/Mid-Cap Momentum Candidates (Aggressive) ─
# ~200 stocks in the $50M-$10B range known for momentum potential.
# Grouped by sector for easier management.
TIER2_AGGRESSIVE: list[str] = [
    # ── Biotech / Pharma (high catalyst potential: FDA, trials) ──
    "CRSP", "BEAM", "EDIT", "NTLA", "RARE", "IONS", "ALNY",
    "ARWR", "FATE", "TWST", "CERT", "RCKT", "APLS", "IMVT",
    "PCVX", "RVMD", "INSM", "XERS", "PRTA", "CRNX", "BMRN",
    "HALO", "SRPT", "VRTX", "REGN", "MRNA", "BNTX", "STVN", "IOVA",

    # ── Tech / Software (growth momentum, SaaS) ────────────────
    "CRWD", "ZS", "NET", "DDOG", "MDB", "SNOW", "CFLT", "ESTC",
    "BILL", "HUBS", "TTD", "ROKU", "U", "PATH", "MNDY", "ASAN",
    "BRZE", "FRSH", "DOCN", "GTLB", "DLO", "QLYS", "TENB",
    "VRNS", "RPD", "CYBR", "PANW", "FTNT", "OKTA", "SAIL", "IOT",

    # ── EV / Clean Energy (sector catalyst plays) ──────────────
    "RIVN", "LCID", "FSLR", "ENPH", "SEDG", "RUN", "ARRY",
    "CHPT", "BLNK", "QS", "PLUG", "BE", "BLDP", "CLNE", "STEM",

    # ── Fintech / Payments ─────────────────────────────────────
    "AFRM", "SOFI", "UPST", "LMND", "ROOT", "HOOD", "MELI",
    "NU", "PAGS", "STNE", "FOUR", "NUVEI",

    # ── E-commerce / Consumer ──────────────────────────────────
    "SHOP", "SE", "PINS", "SNAP", "ETSY", "W", "CHWY", "RVLV",
    "REAL", "VTEX", "MNSO", "CPNG", "GLBE",

    # ── Space / Defense / Aerospace ────────────────────────────
    "RKLB", "ASTS", "BKSY", "RDW", "SPIR", "LUNR",
    "KTOS", "PLTR", "JOBY",

    # ── Semiconductors (small-mid) ─────────────────────────────
    "WOLF", "ACLS", "ONTO", "FORM", "CEVA", "SITM", "AMBA", "SMTC",
    "LSCC", "SLAB", "MRVL", "SWKS", "QRVO", "DIOD", "POWI",

    # ── Mining / Commodities (cyclical momentum) ───────────────
    "MP", "LAC", "SLI", "UUUU", "CCJ", "DNN",
    "NXE", "UEC", "LEU", "VALE", "RIO",

    # ── Meme / High Short Interest (squeeze potential) ─────────
    "GME", "AMC", "ATER", "GSAT",

    # ── Cannabis (regulatory catalyst) ─────────────────────────
    "TLRY", "CRON", "GRWG",

    # ── AI / Robotics / Frontier Tech ──────────────────────────
    "BBAI", "PRCT", "ISRG", "SSYS", "NNOX", "INVZ", "LIDR", "MVIS",

    # ── Healthcare / MedTech (small) ───────────────────────────
    "DXCM", "NVST", "ALGN", "TNDM", "PODD",
    "INSP", "GKOS", "IRTC",
]

# De-duplicate Tier 2 (some may appear twice across sectors)
TIER2_AGGRESSIVE = list(dict.fromkeys(TIER2_AGGRESSIVE))

# ── Combined ────────────────────────────────────────────────
ALL_TICKERS: list[str] = list(dict.fromkeys(TIER1_SAFE + TIER2_AGGRESSIVE))


def get_tier(ticker: str) -> str:
    """Return 'SAFE' or 'AGGRESSIVE' based on which tier the ticker belongs to."""
    if ticker in TIER1_SAFE:
        return "SAFE"
    if ticker in TIER2_AGGRESSIVE:
        return "AGGRESSIVE"
    return "AGGRESSIVE"  # unknown tickers default to aggressive


def get_safe_watchlist() -> list[str]:
    """Return only Tier 1 safe blue-chip tickers."""
    return list(TIER1_SAFE)


def get_aggressive_watchlist() -> list[str]:
    """Return only Tier 2 aggressive small/mid-cap tickers."""
    return list(TIER2_AGGRESSIVE)


def get_full_watchlist() -> list[str]:
    """Return both tiers combined (de-duplicated)."""
    return list(ALL_TICKERS)


def watchlist_summary() -> dict:
    """Return a summary of the watchlist composition."""
    return {
        "tier1_count": len(TIER1_SAFE),
        "tier2_count": len(TIER2_AGGRESSIVE),
        "total": len(ALL_TICKERS),
    }