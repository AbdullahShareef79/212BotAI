"""Comprehensive AI analysis via OpenAI gpt-4o-mini.

Goes far beyond simple sentiment – asks GPT to evaluate:
  • News sentiment
  • Earnings growth trajectory
  • P/E ratio vs sector average
  • Insider buying / selling activity
  • Analyst consensus & price targets
  • Overall confidence score 0-100
  • Predicted price target & timeframe
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an elite equity research analyst with deep expertise in fundamental
analysis, technical analysis, and market sentiment.

You will receive recent news headlines and descriptions for a specific stock.
Analyse them thoroughly and respond with ONLY a JSON object (no markdown fences,
no explanation outside the JSON):

{
  "sentiment": "<POSITIVE | NEGATIVE | NEUTRAL>",
  "sentiment_score": <float -1.0 to +1.0>,

  "earnings_outlook": "<IMPROVING | STABLE | DECLINING>",
  "earnings_reasoning": "<one sentence about earnings growth trajectory>",

  "valuation": "<UNDERVALUED | FAIR | OVERVALUED>",
  "pe_vs_sector": "<BELOW_AVERAGE | AVERAGE | ABOVE_AVERAGE>",
  "valuation_reasoning": "<one sentence about P/E or valuation vs sector>",

  "insider_activity": "<NET_BUYING | NEUTRAL | NET_SELLING>",
  "insider_reasoning": "<one sentence if any insider info found in news>",

  "analyst_consensus": "<STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL>",
  "analyst_reasoning": "<one sentence about analyst targets / upgrades / downgrades>",

  "confidence": <integer 0-100>,
  "confidence_reasoning": "<why this confidence level>",

  "price_target": <float or null>,
  "price_target_timeframe": "<e.g. '3 months', '6 months', null>",

  "summary": "<2-3 sentence overall investment thesis>",

  "risk_factors": ["<risk 1>", "<risk 2>"]
}

Confidence scoring guide:
  0-30:   Very uncertain, conflicting signals, avoid
  31-50:  Below average conviction, probably hold
  51-74:  Moderate conviction but not strong enough to act
  75-85:  Good conviction – reasonable entry if technicals agree
  86-95:  High conviction – strong buy signal
  96-100: Exceptional conviction – rare, very compelling setup

Be honest and conservative. If you lack information on a field, make
your best inference from the available headlines and state your uncertainty
in the reasoning. Never fabricate specific numbers for P/E ratios or price
targets – estimate or return null.
"""


@dataclass
class AnalysisResult:
    """Full AI analysis result for a single stock."""
    # Sentiment
    sentiment: str  # POSITIVE / NEGATIVE / NEUTRAL
    sentiment_score: float  # -1.0 … +1.0

    # Fundamentals
    earnings_outlook: str  # IMPROVING / STABLE / DECLINING
    earnings_reasoning: str
    valuation: str  # UNDERVALUED / FAIR / OVERVALUED
    pe_vs_sector: str  # BELOW_AVERAGE / AVERAGE / ABOVE_AVERAGE
    valuation_reasoning: str

    # Insider
    insider_activity: str  # NET_BUYING / NEUTRAL / NET_SELLING
    insider_reasoning: str

    # Analyst
    analyst_consensus: str  # STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL
    analyst_reasoning: str

    # Confidence
    confidence: int  # 0-100
    confidence_reasoning: str

    # Price target
    price_target: float | None
    price_target_timeframe: str | None

    # Summary
    summary: str
    risk_factors: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def is_positive(self) -> bool:
        return self.sentiment == "POSITIVE"

    @property
    def is_negative(self) -> bool:
        return self.sentiment == "NEGATIVE"

    @property
    def is_high_confidence(self) -> bool:
        """Confidence >= 75 – the buy threshold."""
        return self.confidence >= 75

    @property
    def bullish(self) -> bool:
        """Combined: positive sentiment AND high confidence."""
        return self.is_positive and self.is_high_confidence


# Backwards-compatible alias so existing code referencing SentimentResult still works
SentimentResult = AnalysisResult


def _default_result(reason: str = "No data") -> AnalysisResult:
    return AnalysisResult(
        sentiment="NEUTRAL", sentiment_score=0.0,
        earnings_outlook="STABLE", earnings_reasoning=reason,
        valuation="FAIR", pe_vs_sector="AVERAGE", valuation_reasoning=reason,
        insider_activity="NEUTRAL", insider_reasoning=reason,
        analyst_consensus="HOLD", analyst_reasoning=reason,
        confidence=0, confidence_reasoning=reason,
        price_target=None, price_target_timeframe=None,
        summary=reason, risk_factors=[],
    )


class SentimentAnalyzer:
    """Wraps OpenAI chat-completion for comprehensive stock analysis."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def analyze(self, ticker: str, headlines: list[dict]) -> AnalysisResult:
        """Full AI analysis for *ticker* based on news headlines."""
        if not headlines:
            log.info("No headlines for %s – returning neutral default", ticker)
            return _default_result("No recent news found.")

        # Build user message
        payload = [
            {"title": h.get("title", ""), "description": h.get("description", "")}
            for h in headlines[:15]
        ]
        user_msg = f"Ticker: {ticker}\nHeadlines:\n{json.dumps(payload, indent=2)}"

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0.15,
                max_tokens=800,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            raw = resp.choices[0].message.content.strip()

            # Strip markdown fences if GPT added them
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            data = json.loads(raw)
            result = AnalysisResult(
                sentiment=data.get("sentiment", "NEUTRAL").upper(),
                sentiment_score=float(data.get("sentiment_score", data.get("score", 0.0))),
                earnings_outlook=data.get("earnings_outlook", "STABLE").upper(),
                earnings_reasoning=data.get("earnings_reasoning", ""),
                valuation=data.get("valuation", "FAIR").upper(),
                pe_vs_sector=data.get("pe_vs_sector", "AVERAGE").upper(),
                valuation_reasoning=data.get("valuation_reasoning", ""),
                insider_activity=data.get("insider_activity", "NEUTRAL").upper(),
                insider_reasoning=data.get("insider_reasoning", ""),
                analyst_consensus=data.get("analyst_consensus", "HOLD").upper(),
                analyst_reasoning=data.get("analyst_reasoning", ""),
                confidence=int(data.get("confidence", 0)),
                confidence_reasoning=data.get("confidence_reasoning", ""),
                price_target=data.get("price_target"),
                price_target_timeframe=data.get("price_target_timeframe"),
                summary=data.get("summary", ""),
                risk_factors=data.get("risk_factors", []),
                raw=raw,
            )
            log.info(
                "AI Analysis %s: %s conf=%d%% earnings=%s valuation=%s analyst=%s | %s",
                ticker, result.sentiment, result.confidence,
                result.earnings_outlook, result.valuation,
                result.analyst_consensus, result.summary[:80],
            )
            return result

        except json.JSONDecodeError:
            log.warning("Could not parse OpenAI JSON for %s: %s", ticker, raw[:300])
            return _default_result("JSON parse error")

        except Exception as exc:
            log.error("OpenAI analysis error for %s: %s", ticker, exc)
            return _default_result(f"API error: {exc}")
