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
import re
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

    # v4 multi-source fields (optional, default-safe)
    time_horizon: int | None = None   # predicted holding period in days
    catalyst: str = ""                # key upcoming catalyst identified by AI

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


# ── Multi-source system prompt (v4) ────────────────────────
_MULTI_SYSTEM_PROMPT = """\
You are an elite quantitative hedge fund analyst with access to
multi-source intelligence. Analyze all provided data sources together
to make a holistic BUY/HOLD/SELL decision. Weight insider buying and
options flow heavily as these represent smart money. Reddit/trends
represent retail momentum. News represents current narrative.

Respond with ONLY a JSON object (no markdown fences, no extra text):

{
  "sentiment": "<POSITIVE | NEGATIVE | NEUTRAL>",
  "sentiment_score": <float -1.0 to +1.0>,

  "earnings_outlook": "<IMPROVING | STABLE | DECLINING>",
  "earnings_reasoning": "<one sentence>",

  "valuation": "<UNDERVALUED | FAIR | OVERVALUED>",
  "pe_vs_sector": "<BELOW_AVERAGE | AVERAGE | ABOVE_AVERAGE>",
  "valuation_reasoning": "<one sentence>",

  "insider_activity": "<NET_BUYING | NEUTRAL | NET_SELLING>",
  "insider_reasoning": "<one sentence interpreting the SEC/insider data>",

  "analyst_consensus": "<STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL>",
  "analyst_reasoning": "<one sentence interpreting the analyst data>",

  "confidence": <integer 0-100>,
  "confidence_reasoning": "<holistic reasoning across ALL data sources>",

  "price_target": <float or null>,
  "price_target_timeframe": "<e.g. '3 months' or null>",

  "catalyst": "<key upcoming catalyst or empty string>",
  "time_horizon": <integer days or null>,

  "summary": "<2-3 sentence holistic investment thesis>",
  "risk_factors": ["<risk 1>", "<risk 2>"]
}

Confidence scoring guide:
  0-30:   Avoid — multiple red flags or conflicting signals
  31-69:  Below threshold — interesting but not compelling enough
  70-85:  Buy territory — multiple positive signals align
  86-100: Very high conviction — smart money + fundamentals + momentum agree

Signal weights (apply cumulatively):
  SEC insider NET_BUYING (Form 4 filings)   → +15 confidence
  Options P/C ratio < 0.7 (heavy calls)     → +10 confidence
  Unusual options volume (3× avg)           → +8  confidence
  Analyst upgrades in last 7 days           → +7  per upgrade (cap at +21)
  Reddit score > 70                         → +5  confidence (retail momentum)
  Google Trends FOMO signal active          → +5  confidence (retail interest)
  Fear & Greed < 30 (Extreme Fear)          → contrarian opportunity, note it
  Fear & Greed > 80 (Extreme Greed)         → caution, market overextended
"""


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


# ── Robust JSON parsing with regex fallback ─────────────

def _regex_extract_str(raw: str, key: str, default: str = "") -> str:
    """Extract a string value for *key* from malformed JSON via regex."""
    m = re.search(rf'"{key}"\s*:\s*"([^"]*?)"', raw, re.DOTALL)
    return m.group(1).strip() if m else default


def _regex_extract_num(raw: str, key: str, default: float = 0.0) -> float:
    """Extract a numeric value for *key* from malformed JSON via regex."""
    m = re.search(rf'"{key}"\s*:\s*([\d.\-+eE]+)', raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return default


def _regex_extract_nullable_num(raw: str, key: str) -> float | None:
    """Extract a numeric value that may be null."""
    m = re.search(rf'"{key}"\s*:\s*(null|[\d.\-+eE]+)', raw, re.IGNORECASE)
    if m:
        val = m.group(1)
        if val.lower() == "null":
            return None
        try:
            return float(val)
        except ValueError:
            pass
    return None


def _regex_extract_list(raw: str, key: str) -> list[str]:
    """Extract a JSON array of strings for *key* via regex."""
    m = re.search(rf'"{key}"\s*:\s*\[([^\]]*?)\]', raw, re.DOTALL)
    if m:
        return re.findall(r'"([^"]+?)"', m.group(1))
    return []


def _fix_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ] that break json.loads()."""
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


def _robust_parse_json(raw: str) -> dict:
    """Try multiple strategies to parse GPT's JSON output.

    1. Standard json.loads()
    2. json.loads() after fixing trailing commas & adding missing brackets
    3. Field-by-field regex extraction as last resort

    Never raises – always returns a dict (possibly with defaults).
    """
    # ── Attempt 1: vanilla parse ─────────────────────────────
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    # ── Attempt 2: fix common GPT mistakes ───────────────────
    fixed = _fix_trailing_commas(raw)
    # Ensure the JSON object is closed
    open_braces = fixed.count("{") - fixed.count("}")
    if open_braces > 0:
        fixed += "}" * open_braces
    open_brackets = fixed.count("[") - fixed.count("]")
    if open_brackets > 0:
        fixed += "]" * open_brackets
    try:
        return json.loads(fixed)
    except (json.JSONDecodeError, ValueError):
        pass

    # ── Attempt 3: regex extraction per field ────────────────
    log.warning("JSON repair failed – falling back to regex extraction")
    return {
        "sentiment":             _regex_extract_str(raw, "sentiment", "NEUTRAL"),
        "sentiment_score":       _regex_extract_num(raw, "sentiment_score", 0.0),
        "earnings_outlook":      _regex_extract_str(raw, "earnings_outlook", "STABLE"),
        "earnings_reasoning":    _regex_extract_str(raw, "earnings_reasoning"),
        "valuation":             _regex_extract_str(raw, "valuation", "FAIR"),
        "pe_vs_sector":          _regex_extract_str(raw, "pe_vs_sector", "AVERAGE"),
        "valuation_reasoning":   _regex_extract_str(raw, "valuation_reasoning"),
        "insider_activity":      _regex_extract_str(raw, "insider_activity", "NEUTRAL"),
        "insider_reasoning":     _regex_extract_str(raw, "insider_reasoning"),
        "analyst_consensus":     _regex_extract_str(raw, "analyst_consensus", "HOLD"),
        "analyst_reasoning":     _regex_extract_str(raw, "analyst_reasoning"),
        "confidence":            _regex_extract_num(raw, "confidence", 0),
        "confidence_reasoning":  _regex_extract_str(raw, "confidence_reasoning"),
        "price_target":          _regex_extract_nullable_num(raw, "price_target"),
        "price_target_timeframe": _regex_extract_str(raw, "price_target_timeframe"),
        "summary":               _regex_extract_str(raw, "summary"),
        "risk_factors":          _regex_extract_list(raw, "risk_factors"),
    }


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

            data = _robust_parse_json(raw)
            result = AnalysisResult(
                sentiment=str(data.get("sentiment", "NEUTRAL")).upper(),
                sentiment_score=float(data.get("sentiment_score", data.get("score", 0.0))),
                earnings_outlook=str(data.get("earnings_outlook", "STABLE")).upper(),
                earnings_reasoning=str(data.get("earnings_reasoning", "")),
                valuation=str(data.get("valuation", "FAIR")).upper(),
                pe_vs_sector=str(data.get("pe_vs_sector", "AVERAGE")).upper(),
                valuation_reasoning=str(data.get("valuation_reasoning", "")),
                insider_activity=str(data.get("insider_activity", "NEUTRAL")).upper(),
                insider_reasoning=str(data.get("insider_reasoning", "")),
                analyst_consensus=str(data.get("analyst_consensus", "HOLD")).upper(),
                analyst_reasoning=str(data.get("analyst_reasoning", "")),
                confidence=int(float(data.get("confidence", 0))),
                confidence_reasoning=str(data.get("confidence_reasoning", "")),
                price_target=data.get("price_target"),
                price_target_timeframe=data.get("price_target_timeframe"),
                summary=str(data.get("summary", "")),
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

        except Exception as exc:
            log.error("OpenAI analysis error for %s: %s", ticker, exc)
            return _default_result(f"API error: {exc}")

    def analyze_multi(
        self,
        ticker: str,
        headlines: list[dict],
        reddit_result=None,
        fear_greed=None,
        insider=None,
        options_flow=None,
        analyst_data=None,
        trends_data=None,
    ) -> AnalysisResult:
        """Multi-source AI analysis: news + Reddit + Fear&Greed + insider + options + analysts + trends.

        Each data source is optional — pass None to skip it.
        Falls back to the standard analysis if only headlines are available.
        Never raises; returns a neutral default on total failure.
        """
        # Build the comprehensive user message
        news_payload = [
            {"title": h.get("title", ""), "description": h.get("description", "")}
            for h in headlines[:15]
        ]
        parts: list[str] = [f"Ticker: {ticker}"]
        parts.append(f"\n### NEWS HEADLINES (last 3 days)\n{json.dumps(news_payload, indent=2)}")

        if reddit_result and not reddit_result.error:
            r_tone = (
                "bullish" if reddit_result.sentiment_avg > 0.1
                else "bearish" if reddit_result.sentiment_avg < -0.1
                else "neutral"
            )
            parts.append(
                f"\n### REDDIT INTELLIGENCE\n"
                f"Score: {reddit_result.reddit_score:.0f}/100 | "
                f"Mentions (24h): {reddit_result.mention_count} | Tone: {r_tone}"
            )
            if reddit_result.top_comments:
                comments_str = "\n".join(f"  - {c[:180]}" for c in reddit_result.top_comments[:3])
                parts.append(f"Top comments:\n{comments_str}")

        if fear_greed and not fear_greed.error:
            if fear_greed.is_buying_opportunity:
                fg_note = "BUYING OPPORTUNITY — extreme fear (contrarian)"
            elif fear_greed.avoid_buying:
                fg_note = "CAUTION — extreme greed (market overextended)"
            else:
                fg_note = "neutral territory"
            parts.append(
                f"\n### FEAR & GREED INDEX\n"
                f"Value: {fear_greed.value}/100 ({fear_greed.label}) — {fg_note}"
            )

        if insider and not insider.error:
            parts.append(
                f"\n### SEC INSIDER ACTIVITY (last 14 days)\n"
                f"Signal: {insider.signal} | {insider.summary}"
            )

        if options_flow and not options_flow.error and options_flow.put_call_ratio is not None:
            unusual = "YES — SMART MONEY SIGNAL" if options_flow.unusual_volume else "No"
            parts.append(
                f"\n### OPTIONS FLOW\n"
                f"Put/Call Ratio: {options_flow.put_call_ratio:.2f} ({options_flow.signal}) | "
                f"Calls: {options_flow.call_volume:,} | Puts: {options_flow.put_volume:,} | "
                f"Unusual Volume: {unusual}"
            )

        if analyst_data and not analyst_data.error:
            parts.append(
                f"\n### ANALYST RATINGS (last 30 days)\n"
                f"Consensus: {analyst_data.consensus} | Score: {analyst_data.analyst_score:+.2f} | "
                f"Buys: {analyst_data.buy_count} | Holds: {analyst_data.hold_count} | "
                f"Sells: {analyst_data.sell_count} | Recent upgrades (7d): {analyst_data.recent_upgrades}"
            )

        if trends_data and not trends_data.error:
            fomo = "YES" if trends_data.fomo_signal else "No"
            parts.append(
                f"\n### GOOGLE TRENDS\n"
                f"Interest Score: {trends_data.current_score:.0f}/100 | "
                f"Week-over-Week: {trends_data.wow_change_pct:+.1f}% | FOMO Signal: {fomo}"
            )

        user_msg = "\n".join(parts)

        # Nothing to analyze at all?
        has_data = bool(headlines) or any([
            reddit_result, fear_greed, insider, options_flow, analyst_data, trends_data
        ])
        if not has_data:
            log.info("No multi-source data for %s — returning neutral default", ticker)
            return _default_result("No data available for multi-source analysis.")

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0.15,
                max_tokens=900,
                messages=[
                    {"role": "system", "content": _MULTI_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            data   = _robust_parse_json(raw)
            result = AnalysisResult(
                sentiment=str(data.get("sentiment", "NEUTRAL")).upper(),
                sentiment_score=float(data.get("sentiment_score", 0.0)),
                earnings_outlook=str(data.get("earnings_outlook", "STABLE")).upper(),
                earnings_reasoning=str(data.get("earnings_reasoning", "")),
                valuation=str(data.get("valuation", "FAIR")).upper(),
                pe_vs_sector=str(data.get("pe_vs_sector", "AVERAGE")).upper(),
                valuation_reasoning=str(data.get("valuation_reasoning", "")),
                insider_activity=str(data.get("insider_activity", "NEUTRAL")).upper(),
                insider_reasoning=str(data.get("insider_reasoning", "")),
                analyst_consensus=str(data.get("analyst_consensus", "HOLD")).upper(),
                analyst_reasoning=str(data.get("analyst_reasoning", "")),
                confidence=int(float(data.get("confidence", 0))),
                confidence_reasoning=str(data.get("confidence_reasoning", "")),
                price_target=data.get("price_target"),
                price_target_timeframe=data.get("price_target_timeframe"),
                summary=str(data.get("summary", "")),
                risk_factors=data.get("risk_factors", []),
                raw=raw,
                time_horizon=(
                    int(data["time_horizon"]) if data.get("time_horizon") else None
                ),
                catalyst=str(data.get("catalyst", "")),
            )
            log.info(
                "Multi-AI %s: %s conf=%d%% insider=%s options=%s analyst=%s | %s",
                ticker, result.sentiment, result.confidence,
                result.insider_activity,
                options_flow.signal if options_flow and not options_flow.error else "N/A",
                result.analyst_consensus,
                result.summary[:80],
            )
            return result

        except Exception as exc:
            log.error("OpenAI multi-source error for %s: %s", ticker, exc)
            return _default_result(f"Multi-source API error: {exc}")
