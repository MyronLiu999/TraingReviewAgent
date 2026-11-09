"""Trade assistant (decision mode): from the current chart to a concrete, checkable Al Brooks trade plan.

The user shows the current chart and asks "where do I get in, where do I get out, should I take this?".
The model reads the chart with a Brooks playbook and returns a structured plan; code then enforces the
parts that must not be left to the model:

- bias must be LONG, SHORT or WAIT; a LONG/SHORT needs an entry condition, stop logic and invalidation
- recommendation must be TAKE, WAIT or NO_TRADE; a WAIT bias can never be a TAKE
- no prices when the screenshot's prices are unreadable and no OHLC data was given
- LONG needs stop < entry < target(s), SHORT needs target(s) < entry < stop, else the prices are dropped
- prices must lie within the range of the given OHLC data (+ a few ATR)
- risk/reward, the trader's equation and the position size are computed by code, never taken from the model
- TAKE is downgraded to WAIT when the trader's equation fails, the reward is too small for the
  probability class, a checklist item is a deal-breaker, the setup grade is too low, or the stop is
  wider than the user's risk budget allows
- percentages not found in the cited Brooks sources are removed from the free text

Analysis only. There is no order, broker or execution code anywhere in this project, and the tool
registry refuses any tool with a trading permission. The user places (or does not place) the order.
"""

import json
import math
import re

from chart_review import ANNOTATION_TYPES, STATUS_ENUM
from coach.study import LABEL_RULES
from knowledge.citations import CLAIMS_SCHEMA, format_claim, references_block, verify_claims, wrap_untrusted

PRICE = {"type": ["number", "null"]}
GRID_Y = {"type": ["number", "null"]}            # 0-1000 grid position on the screenshot (top = 0)
PROB_CLASSES = ["high", "even", "low"]
# Representative probabilities of the classes, used only by code for the trader's equation.
# [DERIVED INTERPRETATION] of Brooks' trader's equation: a with-trend setup in a strong trend with a
# good signal bar ~60%+, breakout mode / unclear ~50%, swing or countertrend attempts ~40% or less.
PROB_VALUE = {"high": 0.6, "even": 0.5, "low": 0.4}
PROB_TEXT = {"high": "高 high（≈60%+）", "even": "中 even（≈50%）", "low": "低 low（≈40%-）"}
# Minimum reward/risk per probability class before a TAKE is allowed (config: [trading] min_rr_*).
MIN_RR = {"high": 1.0, "even": 1.5, "low": 2.0}
QUALITY_RANK = {"A": 3, "B": 2, "C": 1, "none": 0}
RECOMMENDATIONS = ["TAKE", "WAIT", "NO_TRADE"]
MARKET_CYCLE = ["strong_bull_trend", "bull_channel", "bull_breakout", "trading_range", "breakout_mode",
                "tight_trading_range", "bear_breakout", "bear_channel", "strong_bear_trend", "unclear"]
CYCLE_TEXT = {"strong_bull_trend": "强多头趋势", "bull_channel": "多头通道", "bull_breakout": "多头突破",
              "trading_range": "交易区间", "breakout_mode": "突破模式", "tight_trading_range": "窄幅交易区间",
              "bear_breakout": "空头突破", "bear_channel": "空头通道", "strong_bear_trend": "强空头趋势",
              "unclear": "不清楚"}
REC_TEXT = {"TAKE": "✅ 建议入场 TAKE", "WAIT": "⏳ 等待触发 WAIT", "NO_TRADE": "⛔ 不建议交易 NO_TRADE"}
BIAS_TEXT = {"LONG": "做多 LONG", "SHORT": "做空 SHORT", "WAIT": "观望 WAIT"}
ORDER_TEXT = {"stop": "突破单 stop order", "limit": "限价单 limit order", "market": "市价单 market", "none": "—"}

# Dollars per 1.0 point, per contract. Overridable in config.toml: [trading.point_values]
DEFAULT_POINT_VALUES = {
    "ES": 50, "MES": 5, "NQ": 20, "MNQ": 2, "YM": 5, "MYM": 0.5, "RTY": 50, "M2K": 5,
    "GC": 100, "MGC": 10, "SI": 5000, "SIL": 1000, "CL": 1000, "MCL": 100, "NG": 10000,
    "6E": 125000, "M6E": 12500, "ZB": 1000, "ZN": 1000,
}

LEG = lambda extra: {"type": "object", "properties": {**extra, "price": PRICE, "y": GRID_Y},
                     "required": list(extra) + ["price", "y"], "additionalProperties": False}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "chart_read": {"type": "object", "properties": {
            "symbol": {"type": "string"}, "timeframe": {"type": "string"}, "last_price": PRICE,
            "last_bar": {"type": "string"}},
            "required": ["symbol", "timeframe", "last_price", "last_bar"], "additionalProperties": False},
        "market_cycle": {"type": "string", "enum": MARKET_CYCLE},
        "market_context": {"type": "string"},
        "always_in": {"type": "string", "enum": ["long", "short", "unclear"]},
        "location": {"type": "string"},
        "bias": {"type": "string", "enum": ["LONG", "SHORT", "WAIT"]},
        "recommendation": {"type": "string", "enum": RECOMMENDATIONS},
        "conclusion": {"type": "string"},
        "setup_quality": {"type": "string", "enum": list(QUALITY_RANK)},
        "probability": {"type": "string", "enum": PROB_CLASSES},
        "probability_reason": {"type": "string"},
        "primary_setup": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ANNOTATION_TYPES + ["none"]},
                           "description": {"type": "string"}, "status": {"type": "string", "enum": STATUS_ENUM}},
            "required": ["name", "type", "description", "status"], "additionalProperties": False},
        "signal_bar": {"type": "object", "properties": {
            "description": {"type": "string"},
            "quality": {"type": "string", "enum": ["strong", "acceptable", "weak", "none"]},
            "x": GRID_Y},
            "required": ["description", "quality", "x"], "additionalProperties": False},
        "entry": LEG({"order_type": {"type": "string", "enum": ["stop", "limit", "market", "none"]},
                      "condition": {"type": "string"}}),
        "stop": LEG({"logic": {"type": "string"}}),
        "target": LEG({"logic": {"type": "string"}}),
        "runner_target": LEG({"logic": {"type": "string"}}),
        "key_levels": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "price": PRICE, "y": GRID_Y},
            "required": ["label", "price", "y"], "additionalProperties": False}},
        "management": {"type": "array", "items": {"type": "string"}},
        "checklist": {"type": "array", "items": {"type": "object", "properties": {
            "item": {"type": "string"}, "verdict": {"type": "string", "enum": ["pass", "warn", "fail"]},
            "note": {"type": "string"}},
            "required": ["item", "verdict", "note"], "additionalProperties": False}},
        "invalidation": {"type": "string"},
        "alternative_scenario": {"type": "string"},
        "wait_for": {"type": "string"},
        "reasons_to_skip": {"type": "string"},
        "watch_next": {"type": "array", "items": {"type": "string"}},
        "reasoning_steps": {"type": "array", "items": {"type": "object", "properties": {
            "step": {"type": "string"}, "observation": {"type": "string"}, "inference": {"type": "string"}},
            "required": ["step", "observation", "inference"], "additionalProperties": False}},
        "follow_up_question": {"type": "string"},
        "claims": CLAIMS_SCHEMA,
        "price_readability": {"type": "string", "enum": ["clear", "partial", "unreadable"]},
        "data_time_range": {"type": "string"},
    },
    "additionalProperties": False,
}
DECISION_SCHEMA["required"] = list(DECISION_SCHEMA["properties"])

# The trading playbook the model works through. It summarizes Brooks' method as working rules
# ([DERIVED INTERPRETATION]); concrete Brooks claims still need retrieved sources (see LABEL_RULES).
PLAYBOOK = """\
# AL BROOKS TRADING PLAYBOOK (work through it in this order)
1. READ THE CHART. Symbol, timeframe, price axis, the 20-bar EMA if shown. The rightmost bar may
   still be forming: say so in `chart_read.last_bar` and treat it as unfinished. If you cannot read
   the symbol or timeframe, write "unknown" (the user's note may name them).
2. CONTEXT FIRST - classify `market_cycle`:
   - strong trend / breakout (spike): consecutive trend bars closing near their extremes, small tails,
     tiny or no pullbacks, gaps away from the EMA, every countertrend attempt fails.
   - channel: trend continues but with deeper two-legged pullbacks, overlapping bars, some
     countertrend bars; the further it goes, the more two-sided it becomes.
   - trading range: overlapping bars, dojis, prominent tails, flat EMA, reversals every few bars,
     breakouts that do not follow through.
   - breakout mode / tight trading range (barbwire): small overlapping bars, no edge for either side.
   Then the always-in direction, and the LOCATION: top / middle / bottom of a range, at the EMA,
   at a prior swing high/low, at a measured-move target, right after a buy or sell climax.
3. WHICH SETUPS FIT THIS CONTEXT:
   - Strong trend: only with-trend entries - H1/H2 (bull) or L1/L2 (bear) pullbacks, the first EMA
     pullback, breakout pullbacks, buying/selling the close of strong trend bars. No countertrend
     trades until there has been a strong countertrend break.
   - Channel: with-trend H2/L2 at the EMA or the trend line; countertrend only as a scalp at the
     channel line with a strong reversal bar; watch for a wedge (three pushes) and a final flag.
   - Trading range: buy low, sell high, scalp. Most breakout attempts fail - fade the extremes after
     a failed breakout or a second entry (double top/bottom, wedge). Avoid the middle of the range.
   - Reversal: a major trend reversal needs (1) a strong break of the trend line, (2) a test of the
     extreme (higher low / lower high / double), (3) a strong signal bar. Otherwise expect only a
     pullback. After a climax expect sideways-to-opposite movement (about ten bars, two legs) first.
   - Tight trading range / barbwire / breakout mode without a breakout: NO_TRADE; wait for a breakout
     with follow-through.
4. SIGNAL BAR AND ENTRY. A good signal bar is a trend bar in the trade direction, closes near its
   extreme, has small tails and is not huge (a huge bar means a big stop and buying high / selling
   low). Grade it in `signal_bar.quality` and give its x position (grid 0-1000). Default entry:
   stop order 1 tick beyond the signal bar (`order_type` "stop"). Limit orders are for trading-range
   extremes or deep pullbacks in strong trends only. "market" only when buying/selling the close of a
   strong breakout bar.
5. STOP. Beyond the signal bar for a scalp, beyond the swing point for a swing. If the logical stop
   is far away the position must be smaller - never a tighter, illogical stop.
6. TARGETS. `target` = the first, conservative target (scalp, at least 1R if probability is high);
   `runner_target` = the swing target for the rest (prior swing high/low, opposite side of the range,
   measured move of the leg / range / spike). Use the price/null rules below; if there is no sensible
   runner, repeat the first target.
7. TRADER'S EQUATION. Choose `probability` honestly: "high" = strong with-trend setup, good signal
   bar, room to the target; "even" = breakout mode, mixed signals, middle of a range; "low" =
   countertrend, reversal attempts, swing targets far away. probability x reward must clearly exceed
   (1 - probability) x risk: a "low" trade needs reward >= 2x risk, an "even" trade about 1.5x, a
   "high" trade at least 1x. Explain it in `probability_reason`. Do NOT write percentages anywhere;
   the system computes the equation from the class and the prices.
8. SHOULD THE USER TAKE IT - `checklist` (pass / warn / fail; fail = deal-breaker):
   context clear; trade direction allowed by the context; location (not the middle of a range, not
   buying at resistance or selling at support in a range); signal bar quality; room to the target vs
   the risk; stop affordable for the user's risk budget (below); no tight trading range / barbwire;
   not right after a climax without a pause; news / session time risk (unknown from a chart = warn).
9. RECOMMENDATION:
   - TAKE: the setup is there (or triggers on the next bar with the stop entry), no checklist fail,
     the trader's equation is positive. Give the complete order: type, entry, stop, targets.
   - WAIT: the context is good but the trigger is not there yet - `wait_for` must name the exact bar,
     pattern or price, and entry/stop/target describe the conditional plan so the user can prepare it.
   - NO_TRADE: unclear, bad location, tight range, risk too large, or only low-quality setups.
     "If in doubt, stay out": a missed trade costs nothing, a bad trade costs money.
   `setup_quality` A = textbook setup in the right context, B = acceptable, C = weak, none = no setup.
10. MANAGEMENT (`management`, 2-5 short steps): when to move the stop to breakeven, where to take
   partial profit (e.g. at the first target), how to trail the runner (below higher lows / above
   lower highs), and when to exit early (e.g. a strong opposite trend bar closing beyond the entry,
   the entry bar failing, or the market not doing what it should within a few bars).
11. Capital first: never widen a stop, no averaging into a loser, no revenge trades. The user's own
   approved rules and risk settings below override generic habits.
"""

DECISION_SYSTEM = """\
You are TradingReview, the user's Al Brooks price action trading assistant. The user shows you the
current chart and wants to know which side to trade, where to enter, where to exit and whether to
take the trade at all. Your job is to help them make money by taking only good trades with
correct risk, and by staying out of bad ones. Be decisive, not reckless:
{style}
- Write the chain of the playbook into `reasoning_steps` (step name, what you see on the chart -
  name the bars so the user can find them -, what you infer), so the user can check and challenge it.
- `follow_up_question`: optionally one question back to the user (e.g. what they see at a bar).
- Prices: give numbers only if you can read them from the price axis or the OHLC facts below.
  Otherwise set price fields to null and describe the level in words (e.g. "1 tick above the
  signal bar high"). Never invent prices, and never claim to know the current live price.
- Positions on the screenshot: the image has a 0-1000 grid (x: left -> right, y: top -> bottom).
  For every level you name (entry, stop, targets, key levels) give its y on that grid so the plan can
  be drawn on the chart; give null if you cannot place it. `key_levels` = up to 4 levels that matter
  (range high/low, prior swing, EMA, measured-move target).
- Judge only with data up to the last bar shown ({data_note}).
- This is analysis only. Never say an order was placed; you cannot place or change orders.
Write in Chinese with English terms.

{playbook}
{label_rules}
# USER'S RISK SETTINGS (computed position size is shown to the user by code)
{risk}

# USER'S APPROVED RULES AND PROFILE
{learning}

# STRUCTURED MARKET DATA (computed from OHLC by code; trust these numbers over the screenshot)
{ohlc}

# RETRIEVED SOURCES
{sources}
"""

DECISIVE = ("Start with the conclusion (direction + TAKE / WAIT / NO_TRADE) in one sentence, then the "
            "evidence, then the invalidation. No repeated generic risk disclaimers.")


def trading_settings(cfg: dict | None) -> dict:
    """The [trading] section of config.toml with defaults (risk is the user's, not the model's)."""
    t = dict((cfg or {}).get("trading") or {})
    pv = {**DEFAULT_POINT_VALUES, **{k.upper(): float(v) for k, v in (t.get("point_values") or {}).items()}}
    return {
        "account_size": float(t.get("account_size", 0) or 0),
        "risk_per_trade_pct": float(t.get("risk_per_trade_pct", 1.0) or 0),
        "max_risk_per_trade": float(t.get("max_risk_per_trade", 0) or 0),
        "max_contracts": int(t.get("max_contracts", 0) or 0),
        "min_setup_quality": str(t.get("min_setup_quality", "B")).upper(),
        "min_rr": {k: float(t.get(f"min_rr_{k}", v)) for k, v in MIN_RR.items()},
        "default_symbol": str(t.get("default_symbol", "") or "").upper(),
        "point_values": pv,
    }


def risk_budget(settings: dict) -> float | None:
    """Dollars the user allows to lose on one trade (None = not configured)."""
    budgets = []
    if settings["account_size"] > 0 and settings["risk_per_trade_pct"] > 0:
        budgets.append(settings["account_size"] * settings["risk_per_trade_pct"] / 100)
    if settings["max_risk_per_trade"] > 0:
        budgets.append(settings["max_risk_per_trade"])
    return min(budgets) if budgets else None


def risk_text(settings: dict) -> str:
    budget = risk_budget(settings)
    if budget is None:
        return ("(not configured: no account size / risk per trade in config.toml. Still judge whether "
                "the stop is reasonable for the setup; the user sizes the position.)")
    return json.dumps({"risk_budget_usd_per_trade": round(budget, 2), "account_size": settings["account_size"],
                       "risk_per_trade_pct": settings["risk_per_trade_pct"],
                       "max_contracts": settings["max_contracts"] or None,
                       "min_setup_quality_to_take": settings["min_setup_quality"],
                       "default_symbol": settings["default_symbol"] or None}, ensure_ascii=False)


def build_system(evidence: list[dict], learning: dict, ohlc_summary: dict | None, decisive: bool, data_note: str,
                 settings: dict | None = None) -> str:
    return DECISION_SYSTEM.format(
        style="- " + DECISIVE if decisive else "- Explain the reasoning first, then the conclusion.",
        data_note=data_note, playbook=PLAYBOOK, label_rules=LABEL_RULES,
        risk=risk_text(settings or trading_settings(None)),
        learning=json.dumps(learning, ensure_ascii=False)[:2500],
        ohlc=json.dumps(ohlc_summary, ensure_ascii=False)[:6000] if ohlc_summary else "(none - screenshot only)",
        sources=wrap_untrusted(evidence) if evidence else "(no sources retrieved)")


def _pct_supported(text: str, canon_text: str) -> str:
    def repl(m):
        return m.group(0) if m.group(0).replace(" ", "") in canon_text.replace(" ", "") else "[未经原书支持的概率已移除]"
    return re.sub(r"\d{1,3}\s?%", repl, text)


def _with_defaults(result: dict) -> dict:
    """Older saved decisions (and partial answers) lack the newer fields; fill neutral values."""
    result.setdefault("chart_read", {"symbol": "", "timeframe": "", "last_price": None, "last_bar": ""})
    result.setdefault("market_cycle", "unclear")
    result.setdefault("location", "")
    result.setdefault("recommendation", "WAIT")
    result.setdefault("setup_quality", "none")
    result.setdefault("probability", "even")
    result.setdefault("probability_reason", "")
    result.setdefault("signal_bar", {"description": "", "quality": "none", "x": None})
    result["entry"].setdefault("order_type", "stop")
    for leg in ("entry", "stop", "target"):
        result[leg].setdefault("y", None)
    result.setdefault("runner_target", {"logic": "", "price": None, "y": None})
    for key in ("key_levels", "management", "checklist", "watch_next", "reasoning_steps", "claims"):
        result.setdefault(key, [])
    for key in ("invalidation", "alternative_scenario", "wait_for", "reasons_to_skip", "follow_up_question",
                "data_time_range", "conclusion", "market_context"):
        result.setdefault(key, "")
    result.setdefault("price_readability", "partial")
    return result


def symbol_root(text: str | None, point_values: dict) -> str | None:
    """'MESZ6' / 'MES 5分钟' / 'mes' -> 'MES' (longest known root that the text starts with or contains)."""
    if not text:
        return None
    up = text.upper()
    words = re.findall(r"[A-Z0-9]{1,8}", up)
    for w in words:                                  # exact root, then root + contract month (MESZ6, ESH27)
        if w in point_values:
            return w
    for w in words:
        for root in sorted(point_values, key=len, reverse=True):
            if w.startswith(root) and re.fullmatch(r"[FGHJKMNQUVXZ]?\d{1,2}!?", w[len(root):] or "-"):
                return root
    return None


def position_size(risk_points: float | None, symbol: str | None, settings: dict) -> dict:
    """Contracts so that (entry - stop) x point value x contracts <= risk budget. Code only."""
    budget = risk_budget(settings)
    out = {"symbol": symbol, "risk_budget": round(budget, 2) if budget is not None else None, "point_value": None,
           "risk_per_contract": None, "contracts": None, "risk_usd": None, "note": ""}
    if budget is None:
        out["note"] = "未设置账户规模/单笔风险（config.toml [trading]），无法计算仓位"
        return out
    if not risk_points:
        out["note"] = "没有可靠的入场/止损价格，无法计算仓位；按止损逻辑和你的单笔风险自行计算"
        return out
    pv = settings["point_values"].get(symbol or "")
    if not pv:
        out["note"] = (f"未知品种 {symbol!r} 的每点价值（在 config.toml [trading.point_values] 添加）" if symbol
                       else "看不出品种，无法计算仓位（在说明里写上品种，例如 MES）")
        return out
    per = risk_points * pv
    n = math.floor(budget / per + 1e-9)
    if settings["max_contracts"]:
        n = min(n, settings["max_contracts"])
    out.update(point_value=pv, risk_per_contract=round(per, 2), contracts=n, risk_usd=round(n * per, 2))
    if n == 0:
        out["note"] = f"止损太宽：1 手风险 ${per:,.2f} 超过单笔风险上限 ${budget:,.2f}"
    return out


def _downgrade(result: dict, issues: list[str], reason: str, to: str = "WAIT") -> None:
    if result["recommendation"] == "TAKE":
        result["recommendation"] = to
        issues.append(f"TAKE → {to}: {reason}")


def validate_decision(result: dict, evidence: list[dict], ohlc_bars=None, settings: dict | None = None,
                      symbol_hint: str | None = None) -> tuple[dict, list[str]]:
    from market.ohlc import price_plausible
    settings = settings or trading_settings(None)
    result = _with_defaults(result)
    issues: list[str] = []
    if result.get("bias") not in ("LONG", "SHORT", "WAIT"):
        issues.append(f"invalid bias {result.get('bias')!r}; set to WAIT")
        result["bias"] = "WAIT"
    if result.get("recommendation") not in RECOMMENDATIONS:
        issues.append(f"invalid recommendation {result.get('recommendation')!r}; set to WAIT")
        result["recommendation"] = "WAIT"
    if result.get("probability") not in PROB_CLASSES:
        result["probability"] = "even"
    if result.get("setup_quality") not in QUALITY_RANK:
        result["setup_quality"] = "none"
    bias = result["bias"]
    entry, stop, target, runner = result["entry"], result["stop"], result["target"], result["runner_target"]
    if bias != "WAIT":
        for name, val in (("entry condition", entry["condition"]), ("stop logic", stop["logic"]),
                          ("invalidation", result["invalidation"])):
            if not val.strip():
                issues.append(f"{bias} without {name}; changed to WAIT")
                result["bias"] = bias = "WAIT"
    elif not result.get("wait_for", "").strip():
        issues.append("WAIT without saying what to wait for")
    if bias == "WAIT" and result["recommendation"] == "TAKE":
        issues.append("TAKE needs a direction (LONG/SHORT); recommendation set to WAIT")
        result["recommendation"] = "WAIT"

    legs = (("entry", entry), ("stop", stop), ("target", target), ("runner target", runner))
    has_data = bool(ohlc_bars)
    if result.get("price_readability") == "unreadable" and not has_data:
        if any(leg["price"] is not None for _, leg in legs):
            issues.append("prices removed: the screenshot's prices are unreadable and no OHLC data was given")
        for _, leg in legs:
            leg["price"] = None
        for lv in result["key_levels"]:
            lv["price"] = None
    if has_data:
        for name, leg in legs:
            if leg["price"] is not None and not price_plausible(leg["price"], ohlc_bars):
                issues.append(f"{name} price {leg['price']} is outside the OHLC data range; removed")
                leg["price"] = None
    e, s, t = entry["price"], stop["price"], target["price"]
    ok = True
    if bias == "LONG" and e is not None:
        ok = (s is None or s < e) and (t is None or t > e)
    elif bias == "SHORT" and e is not None:
        ok = (s is None or s > e) and (t is None or t < e)
    elif bias == "WAIT" and e is not None and s is not None and e == s:
        ok = False
    if not ok:
        issues.append(f"entry/stop/target are inconsistent for {bias} ({e}/{s}/{t}); prices removed")
        for _, leg in legs:
            leg["price"] = None
        e = s = t = None
    r2 = runner["price"]
    if r2 is not None and e is not None and ((bias == "LONG" and r2 <= e) or (bias == "SHORT" and r2 >= e)):
        issues.append(f"runner target {r2} is on the wrong side of the entry; removed")
        runner["price"] = r2 = None
    for leg in (entry, stop, target, runner):
        if leg.get("y") is not None and not 0 <= leg["y"] <= 1000:
            leg["y"] = None
    if result["signal_bar"].get("x") is not None and not 0 <= result["signal_bar"]["x"] <= 1000:
        result["signal_bar"]["x"] = None

    # --- risk / reward and the trader's equation (code, not the model)
    rr = rr2 = None
    if e is not None and s is not None and e != s:
        if t is not None:
            rr = round(abs(t - e) / abs(e - s), 2)
        if r2 is not None:
            rr2 = round(abs(r2 - e) / abs(e - s), 2)
    result["risk_reward"] = rr
    result["runner_rr"] = rr2
    result["risk_points"] = round(abs(e - s), 4) if e is not None and s is not None else None
    p = PROB_VALUE[result["probability"]]
    need = settings["min_rr"][result["probability"]]
    eq = None
    if rr is not None:
        expectancy = round(p * rr - (1 - p), 2)
        eq = {"probability_class": result["probability"], "probability": p, "reward_risk": rr,
              "min_reward_risk": need, "expectancy_r": expectancy, "ok": expectancy > 0 and rr >= need}
        if not eq["ok"]:
            _downgrade(result, issues, f"trader's equation fails (R/R {rr} for {result['probability']} probability, "
                                       f"need ≥ {need}; expectancy {expectancy}R)")
    result["traders_equation"] = eq

    # --- the checklist, setup grade and stop size can veto a TAKE
    fails = [c["item"] for c in result["checklist"] if c.get("verdict") == "fail"]
    if fails:
        _downgrade(result, issues, "checklist deal-breaker: " + "; ".join(fails[:3]))
    min_q = settings["min_setup_quality"] if settings["min_setup_quality"] in QUALITY_RANK else "B"
    if QUALITY_RANK[result["setup_quality"]] < QUALITY_RANK[min_q]:
        _downgrade(result, issues, f"setup quality {result['setup_quality']} is below your minimum {min_q}")
    sym = (symbol_root(result["chart_read"].get("symbol"), settings["point_values"])
           or symbol_root(symbol_hint, settings["point_values"]) or (settings["default_symbol"] or None))
    size = position_size(result["risk_points"], sym, settings)
    result["position"] = size
    if size["contracts"] == 0:
        _downgrade(result, issues, size["note"], to="NO_TRADE")

    canon = " ".join(ev.get("full_text") or ev["excerpt"] for ev in evidence if ev["authority"] == "canonical")
    for key in ("conclusion", "market_context", "invalidation", "alternative_scenario", "wait_for", "reasons_to_skip",
                "location", "probability_reason"):
        cleaned = _pct_supported(result.get(key, ""), canon)
        if cleaned != result.get(key, ""):
            issues.append(f"unsupported probability removed from {key}")
            result[key] = cleaned
    for leg in (entry, stop, target, runner):
        for k in ("condition", "logic"):
            if k in leg:
                cleaned = _pct_supported(leg[k], canon)
                if cleaned != leg[k]:
                    issues.append("unsupported probability removed from the trade plan")
                    leg[k] = cleaned
    result["management"] = [_pct_supported(m, canon) for m in result["management"]]
    claims, claim_issues = verify_claims(result.get("claims", []), {ev["evidence_id"]: ev for ev in evidence})
    result["claims"] = claims
    issues += [f"claim {i['claim']}: {i['from']} -> {i['to']} ({'; '.join(i['flags'])})" for i in claim_issues
               if i["from"] != i["to"]]
    result["execution"] = "analysis only - no order was placed or changed"
    return result, issues


def headline(r: dict, plain: bool = False) -> str:
    """One line for history lists, alerts and (plain = no emoji, for chart fonts) the chart header."""
    rr = f" · R/R {r['risk_reward']:g}" if r.get("risk_reward") else ""
    q = f" · {r.get('setup_quality')}级" if r.get("setup_quality") not in (None, "none") else ""
    rec = REC_TEXT.get(r.get("recommendation"), "")
    if plain:
        rec = rec.split(" ", 1)[-1]
    return f"{BIAS_TEXT.get(r['bias'], r['bias'])} · {rec}{q}{rr}"


def format_decision(r: dict, evidence: list[dict], issues: list[str], data_quality: dict) -> str:
    ev = {e["evidence_id"]: e for e in evidence}
    price = lambda v: f"{v:g}" if isinstance(v, (int, float)) else "—（无可靠价格，按条件执行）"
    eq, pos = r.get("traders_equation"), r.get("position") or {}
    cr_ = r.get("chart_read") or {}
    lines = [
        f"**BIAS 倾向：{headline(r)}**", r["conclusion"], "",
        f"品种/周期：{cr_.get('symbol') or '未知'} {cr_.get('timeframe') or ''}；"
        f"阶段：{CYCLE_TEXT.get(r.get('market_cycle'), r.get('market_cycle'))}；Always In：{r['always_in']}；"
        f"Setup：{r.get('setup_quality')} 级；概率等级：{PROB_TEXT.get(r.get('probability'), '—')}", "",
        "**📌 交易计划 TRADE PLAN**",
        f"- 入场 ENTRY（{ORDER_TEXT.get(r['entry'].get('order_type'), '—')}）：{r['entry']['condition']}；价格 {price(r['entry']['price'])}",
        f"- 止损 STOP：{r['stop']['logic']}；价格 {price(r['stop']['price'])}",
        f"- 目标1 TARGET：{r['target']['logic']}；价格 {price(r['target']['price'])}"
        + (f"（{r['risk_reward']:g}R）" if r.get("risk_reward") else ""),
    ]
    if r.get("runner_target", {}).get("logic"):
        lines.append(f"- 目标2 RUNNER：{r['runner_target']['logic']}；价格 {price(r['runner_target']['price'])}"
                     + (f"（{r['runner_rr']:g}R）" if r.get("runner_rr") else ""))
    lines.append("")
    lines.append("**⚖️ RISK / REWARD · 交易者方程 TRADER'S EQUATION**（程序计算）")
    if eq:
        lines.append(f"概率 {eq['probability']:.0%} × 回报 {eq['reward_risk']:g}R − 失败 {1 - eq['probability']:.0%} × 1R "
                     f"= 期望 {eq['expectancy_r']:+g}R；该概率等级至少需要 {eq['min_reward_risk']:g}R → "
                     + ("✅ 成立" if eq["ok"] else "❌ 不成立"))
    else:
        lines.append("价格不足，无法计算；按止损逻辑控制风险")
    if r.get("probability_reason"):
        lines.append(f"概率理由：{r['probability_reason']}")
    lines += ["", "**💰 仓位 POSITION SIZE**（程序计算）"]
    if pos.get("contracts") is not None:
        lines.append(f"{pos['symbol']}：每点 ${pos['point_value']:g}；止损 {r['risk_points']:g} 点 = 每手 ${pos['risk_per_contract']:,.2f}；"
                     f"单笔风险上限 ${pos['risk_budget']:,.2f} → **{pos['contracts']} 手**（实际风险 ${pos['risk_usd']:,.2f}）")
    if pos.get("note"):
        lines.append(pos["note"])
    lines.append("")
    if r.get("checklist"):
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
        lines += ["**🧾 该不该做 CHECKLIST**"] + [f"{icon.get(c['verdict'], '·')} {c['item']}：{c['note']}"
                                                for c in r["checklist"]] + [""]
    if r.get("management"):
        lines += ["**🛠 持仓管理 MANAGEMENT**"] + [f"{i}. {m}" for i, m in enumerate(r["management"], 1)] + [""]
    if r.get("reasoning_steps"):
        lines += ["**推理过程 REASONING**"] + [f"{i}. {s['step']}：{s['observation']} → {s['inference']}"
                                            for i, s in enumerate(r["reasoning_steps"], 1)] + [""]
    lines += [
        "**MARKET CONTEXT 市场背景**", r["market_context"] + (f"\n位置：{r['location']}" if r.get("location") else ""), "",
        "**PRIMARY SETUP 主要结构**", f"{r['primary_setup']['name']}（{r['primary_setup']['status']}）：{r['primary_setup']['description']}"
        + (f"\n信号K线（{r['signal_bar']['quality']}）：{r['signal_bar']['description']}" if r.get("signal_bar", {}).get("description") else ""), "",
        "**INVALIDATION 失效条件**", r["invalidation"], "",
        "**ALTERNATIVE SCENARIO 备选情景**", r["alternative_scenario"], "",
    ]
    if r["bias"] == "WAIT" or r.get("recommendation") != "TAKE" or r.get("wait_for"):
        lines += ["**WAIT FOR 等待什么**", r.get("wait_for") or "—", ""]
    if r.get("reasons_to_skip"):
        lines += ["**放弃交易的理由**", r["reasons_to_skip"], ""]
    if r.get("watch_next"):
        lines += ["**接下来观察**"] + [f"- {w}" for w in r["watch_next"]] + [""]
    lines.append("**BROOKS EVIDENCE 依据**")
    for c in r.get("claims", []):
        lines.append("- " + format_claim(c, ev))
    lines += ["", "**DATA QUALITY 数据质量**",
              f"数据时间：{data_quality.get('time_range') or r.get('data_time_range') or '未知'}；"
              f"价格可读性：{r.get('price_readability')}；来源：{data_quality.get('source')}；"
              f"{data_quality.get('note', '')}",
              "仅为分析建议，系统没有也不能下单；是否下单、下多少由你决定。"]
    if r.get("follow_up_question"):
        lines += ["", f"🤔 **我想问你**：{r['follow_up_question']}"]
    lines += ["", "（可以继续追问或质疑任何一步，例如「为什么止损放在这里？」「我觉得这里该偏空」。）"]
    if issues:
        lines += ["", "🔎 校验：" + "；".join(issues)]
    if evidence:
        lines += ["", "**参考来源**", references_block(evidence)]
    return "\n".join(lines).strip()
