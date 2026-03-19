"""GPT-powered catalyst detection and scoring for momentum plays.

Asks GPT to identify and score specific catalysts:
  • FDA approvals / drug trial results
  • Government contracts / major partnerships
  • Earnings surprises (beat/miss magnitude)
  • Short squeeze potential (high SI%, days to cover)
  • M&A rumours / activist investor involvement
  • Sector-wide catalysts (regulation, macro shifts)

Returns a catalyst score 1-10. Only enter momentum plays with score > 7.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI

log = logging.getLogger(__name__)

_CATALYST_SYSTEM_PROMPT = """\
You are an expert stock market catalyst analyst specialising in identifying
short-term price catalysts for momentum trading.

You will receive a stock ticker, its recent price action data, and news headlines.
Your job is to identify ALL active catalysts and score them.

Respond with ONLY a JSON object (no markdown fences):

{
  "catalyst_score": <integer 1-10>,
  "primary_catalyst": "<the single strongest catalyst type>",
  "catalyst_type": "<FDA | CONTRACT | EARNINGS_SURPRISE | SHORT_SQUEEZE | MA_RUMOUR | ACTIVIST | SECTOR_CATALYST | UPGRADE | PRODUCT_LAUNCH | PARTNERSHIP | RESTRUCTURING | OTHER>",
  "catalysts": [
    {
      "type": "<catalyst type from list above>",
      "description": "<one sentence describing the catalyst>",
      "strength": <integer 1-10>,
      "timeframe": "<IMMEDIATE | DAYS | WEEKS | MONTHS>"
    }
  ],
  "short_interest_risk": "<LOW | MODERATE | HIGH | SQUEEZE_POTENTIAL>",
  "earnings_surprise_pct": <float or null>,
  "institutional_activity": "<ACCUMULATING | NEUTRAL | DISTRIBUTING | UNKNOWN>",
  "momentum_sustainability": "<HIGH | MODERATE | LOW>",
  "entry_urgency": "<IMMEDIATE | TODAY | THIS_WEEK | NO_RUSH>",
  "risk_reward_ratio": "<EXCELLENT | GOOD | FAIR | POOR>",
  "reasoning": "<2-3 sentences explaining the overall catalyst thesis>",
  "red_flags": ["<warning 1>", "<warning 2>"]
}

Catalyst scoring guide:
  1-3:  Weak / no clear catalyst, likely random movement
  4-5:  Minor catalyst, limited upside potential
  6:    Decent catalyst but not strong enough for aggressive entry
  7:    Good catalyst – worth entering with moderate position
  8:    Strong catalyst – high conviction entry
  9:    Very strong catalyst – multiple converging catalysts
  10:   Exceptional – once-in-a-quarter type catalyst (FDA approval, massive earnings beat, etc.)

Be brutally honest. Most momentum moves are noise (score 1-4).
Only score 7+ when you can identify a specific, verifiable catalyst.
If headlines are thin or vague, default to a low score.
"""


@dataclass
class CatalystDetail:
    """A single identified catalyst."""
    type: str
    description: str
    strength: int  # 1-10
    timeframe: str  # IMMEDIATE / DAYS / WEEKS / MONTHS


@dataclass
class CatalystResult:
    """Full catalyst analysis for a momentum stock."""
    ticker: str
    catalyst_score: int  # 1-10
    primary_catalyst: str
    catalyst_type: str
    catalysts: list[CatalystDetail] = field(default_factory=list)
    short_interest_risk: str = "UNKNOWN"
    earnings_surprise_pct: float | None = None
    institutional_activity: str = "UNKNOWN"
    momentum_sustainability: str = "LOW"
    entry_urgency: str = "NO_RUSH"
    risk_reward_ratio: str = "FAIR"
    reasoning: str = ""
    red_flags: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def is_actionable(self) -> bool:
        """Score >= 7 – worth entering an aggressive position."""
        return self.catalyst_score >= 7

    @property
    def is_high_conviction(self) -> bool:
        """Score >= 8 – high conviction, larger position sizing."""
        return self.catalyst_score >= 8

    @property
    def is_exceptional(self) -> bool:
        """Score >= 9 – exceptional, maximum position sizing."""
        return self.catalyst_score >= 9

    @property
    def conviction_tier(self) -> str:
        """Maps catalyst score to conviction tier for position sizing."""
        if self.catalyst_score >= 9:
            return "MAX"      # 25% of portfolio
        elif self.catalyst_score >= 8:
            return "HIGH"     # 15% of portfolio
        elif self.catalyst_score >= 7:
            return "MODERATE"  # 5% of portfolio
        return "NONE"


def _default_catalyst(ticker: str, reason: str = "No data") -> CatalystResult:
    return CatalystResult(
        ticker=ticker,
        catalyst_score=1,
        primary_catalyst="NONE",
        catalyst_type="OTHER",
        reasoning=reason,
    )


class CatalystAnalyzer:
    """Wraps OpenAI for catalyst detection and scoring."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def analyze(
        self,
        ticker: str,
        headlines: list[dict],
        price_context: str = "",
    ) -> CatalystResult:
        """Analyze catalysts for a momentum stock.

        Args:
            ticker: Stock symbol
            headlines: Recent news headlines
            price_context: String describing recent price action
                          (e.g., "Up 12% today, 3.5x volume, near 52w high")
        """
        if not headlines:
            log.info("No headlines for catalyst analysis on %s", ticker)
            return _default_catalyst(ticker, "No news to analyze")

        payload = [
            {"title": h.get("title", ""), "description": h.get("description", "")}
            for h in headlines[:15]
        ]

        user_msg = (
            f"Ticker: {ticker}\n"
            f"Price Action: {price_context}\n"
            f"Headlines:\n{json.dumps(payload, indent=2)}"
        )

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0.1,
                max_tokens=900,
                messages=[
                    {"role": "system", "content": _CATALYST_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            raw = resp.choices[0].message.content.strip()

            # Strip markdown fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            data = json.loads(raw)

            # Parse individual catalysts
            catalysts = []
            for c in data.get("catalysts", []):
                catalysts.append(CatalystDetail(
                    type=c.get("type", "OTHER"),
                    description=c.get("description", ""),
                    strength=int(c.get("strength", 1)),
                    timeframe=c.get("timeframe", "UNKNOWN"),
                ))

            result = CatalystResult(
                ticker=ticker,
                catalyst_score=int(data.get("catalyst_score", 1)),
                primary_catalyst=data.get("primary_catalyst", "NONE"),
                catalyst_type=data.get("catalyst_type", "OTHER"),
                catalysts=catalysts,
                short_interest_risk=data.get("short_interest_risk", "UNKNOWN"),
                earnings_surprise_pct=data.get("earnings_surprise_pct"),
                institutional_activity=data.get("institutional_activity", "UNKNOWN"),
                momentum_sustainability=data.get("momentum_sustainability", "LOW"),
                entry_urgency=data.get("entry_urgency", "NO_RUSH"),
                risk_reward_ratio=data.get("risk_reward_ratio", "FAIR"),
                reasoning=data.get("reasoning", ""),
                red_flags=data.get("red_flags", []),
                raw=raw,
            )

            log.info(
                "Catalyst %s: score=%d type=%s primary=%s sustainability=%s | %s",
                ticker, result.catalyst_score, result.catalyst_type,
                result.primary_catalyst, result.momentum_sustainability,
                result.reasoning[:80],
            )
            return result

        except json.JSONDecodeError:
            log.warning("Could not parse catalyst JSON for %s: %s", ticker, raw[:300])
            return _default_catalyst(ticker, "JSON parse error")

        except Exception as exc:
            log.error("Catalyst analysis error for %s: %s", ticker, exc)
            return _default_catalyst(ticker, f"API error: {exc}")
