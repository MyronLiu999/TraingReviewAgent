"""Sends batches of headlines to an OpenAI model and returns structured market-impact verdicts."""

import json
import logging
from datetime import datetime, timezone
from typing import Literal

import openai
from pydantic import BaseModel, ValidationError

from feeds import NewsItem

log = logging.getLogger(__name__)

# Kept byte-for-byte stable so OpenAI can cache it automatically. Anything that changes
# (markets, time, headlines) goes in the user message instead.
SYSTEM_PROMPT = """\
You are a real-time financial news impact analyst working for an active day trader.
You receive a batch of news items pulled automatically from RSS feeds. For every item,
judge whether it is POSITIVE, NEGATIVE, NEUTRAL, or MIXED for each requested market,
how strong the impact is likely to be, and how much to trust it.
You do NOT give buy/sell orders. You give a fast, honest, structured read.

# HOW TO ANALYZE EACH ITEM
1. Extract the fact. What actually happened (or is claimed)? Separate fact from opinion.
   Is it new information or recycled old news? Use the published time you are given.
2. Source credibility. Each item comes with a source tier:
   Tier 1 = official sources / top wires, Tier 2 = established financial media,
   Tier 3 = aggregators, unverified or social content. Headlines quoting "sources say",
   anonymous claims, or sensational wording deserve lower confidence. Set
   misinformation_risk=true when an item looks like a rumor, pump, or fabricated claim.
3. Transmission channel. Why would this move the market?
   EQUITY drivers: growth data (GDP, PMIs, jobs, retail sales); inflation and rates
   (CPI/PCE/PPI, central-bank tone, Treasury yields; higher yields usually hurt valuations,
   especially tech); earnings and guidance, especially index-heavy mega-caps; liquidity and
   risk appetite (QE/QT, credit spreads, VIX, bank stress); policy (tariffs, taxes,
   regulation, shutdowns, elections); geopolitics and energy shocks (usually risk-off).
   Watch for the "good news is bad news" regime, where strong data is negative because it
   implies a more hawkish Fed.
   GOLD drivers: real interest rates (lower = positive); US dollar (stronger = usually
   negative); Fed expectations (dovish = positive, hawkish = negative); inflation surprises
   (usually positive unless they make the Fed much more hawkish); safe-haven demand
   (war, crisis, bank stress = positive); central-bank buying, ETF flows, China/India
   physical demand; strong risk-on euphoria can be mildly negative.
   For a single stock, ETF, or other custom market, use the drivers relevant to it.
4. Surprise vs. expectations. Markets move on surprise, not on the headline itself.
   For data releases compare actual vs. consensus vs. prior when the text gives them;
   never invent consensus numbers. Consider whether it is already priced in.
5. Magnitude and timing. How big is this versus normal daily news flow? Is the effect
   immediate, intraday, or multi-day? Could it reverse quickly?

# MULTIPLE MARKETS
Reason about each requested market separately. The same news is often opposite for
different markets (a geopolitical shock is often NEGATIVE for equities and POSITIVE for gold).

# RELEVANCE
Most feed items are noise for a trader (lifestyle, personal finance tips, minor company
news, opinion columns). For those set relevant=false and return an empty analyses list.
Only set relevant=true if the item could plausibly move at least one requested market.

# SCORING
impact: 1 = noise, 2 = minor, 3 = noticeable intraday move, 4 = strong move,
5 = major market-moving event (e.g. surprise Fed decision, war outbreak, big CPI miss).
Be conservative: most relevant items are 1-2. Reserve 4-5 for genuinely large surprises.
confidence: LOW when the information is ambiguous, unconfirmed, or the direction is unclear.
Prefer NEUTRAL or MIXED with LOW confidence over forcing a direction.

# OUTPUT
Return one entry per input item, using the same id. Keep every text field short:
summary = one neutral sentence; reasons = at most 3 short bullets describing the chain
news -> driver -> market; risks = at most 2 short bullets on what could make the call wrong;
watch = at most 3 short items to confirm the move (e.g. 10Y yield, DXY, VIX, futures).
Never fabricate data, prices, quotes or sources.
"""

Verdict = Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"]


class MarketAnalysis(BaseModel):
    market: str
    verdict: Verdict
    impact: int
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    timing: Literal["immediate", "intraday", "multi_day"]
    priced_in: Literal["yes", "partly", "no"]
    reasons: list[str]
    risks: list[str]
    watch: list[str]


class ItemResult(BaseModel):
    id: int
    relevant: bool
    summary: str
    stale: bool
    misinformation_risk: bool
    analyses: list[MarketAnalysis]


class BatchResult(BaseModel):
    items: list[ItemResult]


_STR_LIST = {"type": "array", "items": {"type": "string"}}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevant": {"type": "boolean"},
                    "summary": {"type": "string"},
                    "stale": {"type": "boolean"},
                    "misinformation_risk": {"type": "boolean"},
                    "analyses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "market": {"type": "string"},
                                "verdict": {"type": "string", "enum": ["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"]},
                                "impact": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                                "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                                "timing": {"type": "string", "enum": ["immediate", "intraday", "multi_day"]},
                                "priced_in": {"type": "string", "enum": ["yes", "partly", "no"]},
                                "reasons": _STR_LIST,
                                "risks": _STR_LIST,
                                "watch": _STR_LIST,
                            },
                            "required": ["market", "verdict", "impact", "confidence", "timing",
                                         "priced_in", "reasons", "risks", "watch"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "relevant", "summary", "stale", "misinformation_risk", "analyses"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def describe_markets(markets: list[str], cfg: dict) -> str:
    lines = []
    for m in markets:
        if m == "EQUITY":
            lines.append(f"- EQUITY (focus: {cfg.get('equity_focus', 'S&P 500')})")
        elif m == "GOLD":
            lines.append(f"- GOLD (focus: {cfg.get('gold_focus', 'XAUUSD')})")
        else:
            lines.append(f"- {m}")
    return "\n".join(lines)


def build_user_message(items: list[NewsItem], markets: list[str], cfg: dict) -> str:
    now = datetime.now(timezone.utc)
    parts = [
        f"Current time (UTC): {now:%Y-%m-%d %H:%M}",
        f"Trading horizon: {cfg.get('horizon', 'INTRADAY')}",
        "Analyze every item for each of these markets (use these exact market names):",
        describe_markets(markets, cfg),
        "",
        "NEWS ITEMS:",
    ]
    for i, it in enumerate(items):
        age = ""
        if it.published:
            mins = int((now - it.published).total_seconds() // 60)
            age = f" | published {it.published:%Y-%m-%d %H:%M} UTC ({mins} min ago)"
        parts.append(f"[id={i}] source: {it.source} (Tier {it.tier}){age}")
        parts.append(f"  headline: {it.title}")
        if it.summary:
            parts.append(f"  snippet: {it.summary}")
    return "\n".join(parts)


class Analyzer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = openai.OpenAI()  # reads OPENAI_API_KEY from the environment

    def analyze(self, items: list[NewsItem], markets: list[str]) -> list[ItemResult | None]:
        """Returns one result per input item (same order; None if the model skipped it). Raises on failure."""
        response = self.client.chat.completions.create(
            model=self.cfg.get("model", "gpt-4o"),
            max_completion_tokens=8000,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(items, markets, self.cfg)},
            ],
            # Structured outputs: the reply is guaranteed to match OUTPUT_SCHEMA
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "news_verdicts", "strict": True, "schema": OUTPUT_SCHEMA},
            },
        )

        choice = response.choices[0]
        if choice.message.refusal:
            raise RuntimeError(f"Model declined to analyze this batch: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise RuntimeError("Response was cut off; lower batch_size in config.toml")
        if not choice.message.content:
            raise RuntimeError("Empty response from model")
        try:
            result = BatchResult.model_validate(json.loads(choice.message.content))
        except (json.JSONDecodeError, ValidationError) as e:
            raise RuntimeError(f"Could not parse model output: {e}") from e

        if response.usage:
            log.info("OpenAI usage: in=%s out=%s",
                     response.usage.prompt_tokens, response.usage.completion_tokens)

        by_id = {r.id: r for r in result.items}
        return [by_id.get(i) for i in range(len(items))]
