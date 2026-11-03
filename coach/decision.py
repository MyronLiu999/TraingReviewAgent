"""Decision support: a clear LONG / SHORT / WAIT call with a checkable, conditional plan.

Analysis only. There is no order, broker or execution code anywhere in this project, and the
tool registry refuses any tool with a trading permission.

validate_decision() enforces the protocol in code (not just in the prompt):
- bias must be LONG, SHORT or WAIT; a LONG/SHORT needs entry condition, stop logic, invalidation
- no prices when the screenshot's prices are unreadable and no OHLC data was given
- LONG needs stop < entry < target, SHORT needs target < entry < stop, else the prices are dropped
- prices must lie within the range of the given OHLC data (+ a few ATR)
- risk/reward is computed by code, never taken from the model
- percentages not found in the cited Brooks sources are removed
"""

import json
import re

from chart_review import ANNOTATION_TYPES, STATUS_ENUM
from coach.study import LABEL_RULES
from knowledge.citations import CLAIMS_SCHEMA, format_claim, references_block, verify_claims, wrap_untrusted

PRICE = {"type": ["number", "null"]}
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "market_context": {"type": "string"},
        "always_in": {"type": "string", "enum": ["long", "short", "unclear"]},
        "bias": {"type": "string", "enum": ["LONG", "SHORT", "WAIT"]},
        "conclusion": {"type": "string"},
        "primary_setup": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ANNOTATION_TYPES + ["none"]},
                           "description": {"type": "string"}, "status": {"type": "string", "enum": STATUS_ENUM}},
            "required": ["name", "type", "description", "status"], "additionalProperties": False},
        "entry": {"type": "object", "properties": {"condition": {"type": "string"}, "price": PRICE},
                  "required": ["condition", "price"], "additionalProperties": False},
        "stop": {"type": "object", "properties": {"logic": {"type": "string"}, "price": PRICE},
                 "required": ["logic", "price"], "additionalProperties": False},
        "target": {"type": "object", "properties": {"logic": {"type": "string"}, "price": PRICE},
                   "required": ["logic", "price"], "additionalProperties": False},
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
    "required": ["market_context", "always_in", "bias", "conclusion", "primary_setup", "entry", "stop", "target",
                 "invalidation", "alternative_scenario", "wait_for", "reasons_to_skip", "watch_next",
                 "reasoning_steps", "follow_up_question", "claims",
                 "price_readability", "data_time_range"],
    "additionalProperties": False,
}

DECISION_SYSTEM = """\
You are the user's Al Brooks price action decision-support analyst. The user is looking at a
live or recent chart and asks which side to favour. Be decisive, not reckless:
{style}
- Work through: context -> trend or trading range -> always-in direction -> current bars ->
  signal bar / entry bar -> setup -> entry trigger -> stop logic -> target -> invalidation ->
  alternative scenario. Write this chain into `reasoning_steps` (step name, what you see on the
  chart, what you infer), so the user can check and challenge every step.
- `follow_up_question`: optionally one question back to the user (e.g. what they see at a bar).
- WAIT is a valid answer when the evidence is not there - then `wait_for` must name the exact
  pattern, price area or signal that would change it. Never pick LONG/SHORT just to avoid WAIT.
- Prices: give numbers only if you can read them from the price axis or the OHLC facts below.
  Otherwise set price fields to null and describe the level in words (e.g. "1 tick above the
  signal bar high"). Never invent prices, and never claim to know the current live price.
- Judge only with data up to the last bar shown ({data_note}).
- No probability numbers unless a cited Brooks source gives them, with its condition.
- This is analysis only. Never say an order was placed; you cannot place or change orders.
Write in Chinese with English terms.

{label_rules}
# USER'S APPROVED RULES AND PROFILE
{learning}

# STRUCTURED MARKET DATA (computed from OHLC by code; trust these numbers over the screenshot)
{ohlc}

# RETRIEVED SOURCES
{sources}
"""

DECISIVE = ("Start with the conclusion (LONG / SHORT / WAIT) in one sentence, then the evidence, "
            "then the invalidation. No repeated generic risk disclaimers.")


def build_system(evidence: list[dict], learning: dict, ohlc_summary: dict | None, decisive: bool, data_note: str) -> str:
    return DECISION_SYSTEM.format(
        style="- " + DECISIVE if decisive else "- Explain the reasoning first, then the conclusion.",
        data_note=data_note, label_rules=LABEL_RULES,
        learning=json.dumps(learning, ensure_ascii=False)[:2500],
        ohlc=json.dumps(ohlc_summary, ensure_ascii=False)[:6000] if ohlc_summary else "(none - screenshot only)",
        sources=wrap_untrusted(evidence) if evidence else "(no sources retrieved)")


def _pct_supported(text: str, canon_text: str) -> str:
    def repl(m):
        return m.group(0) if m.group(0).replace(" ", "") in canon_text.replace(" ", "") else "[未经原书支持的概率已移除]"
    return re.sub(r"\d{1,3}\s?%", repl, text)


def validate_decision(result: dict, evidence: list[dict], ohlc_bars=None) -> tuple[dict, list[str]]:
    from market.ohlc import price_plausible
    issues: list[str] = []
    if result.get("bias") not in ("LONG", "SHORT", "WAIT"):
        issues.append(f"invalid bias {result.get('bias')!r}; set to WAIT")
        result["bias"] = "WAIT"
    bias = result["bias"]
    entry, stop, target = result["entry"], result["stop"], result["target"]
    if bias != "WAIT":
        for name, val in (("entry condition", entry["condition"]), ("stop logic", stop["logic"]),
                          ("invalidation", result["invalidation"])):
            if not val.strip():
                issues.append(f"{bias} without {name}; changed to WAIT")
                result["bias"] = bias = "WAIT"
    elif not result.get("wait_for", "").strip():
        issues.append("WAIT without saying what to wait for")

    has_data = bool(ohlc_bars)
    if result.get("price_readability") == "unreadable" and not has_data:
        for leg in (entry, stop, target):
            if leg["price"] is not None:
                issues.append("prices removed: the screenshot's prices are unreadable and no OHLC data was given")
                break
        entry["price"] = stop["price"] = target["price"] = None
    if has_data:
        for name, leg in (("entry", entry), ("stop", stop), ("target", target)):
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
        entry["price"] = stop["price"] = target["price"] = None
        e = s = t = None
    rr = None
    if e is not None and s is not None and t is not None and e != s:
        rr = round(abs(t - e) / abs(e - s), 2)
    result["risk_reward"] = rr
    result["risk_points"] = round(abs(e - s), 4) if e is not None and s is not None else None

    canon = " ".join(ev.get("full_text") or ev["excerpt"] for ev in evidence if ev["authority"] == "canonical")
    for key in ("conclusion", "market_context", "invalidation", "alternative_scenario", "wait_for", "reasons_to_skip"):
        cleaned = _pct_supported(result.get(key, ""), canon)
        if cleaned != result.get(key, ""):
            issues.append(f"unsupported probability removed from {key}")
            result[key] = cleaned
    for leg in (entry, stop, target):
        for k in ("condition", "logic"):
            if k in leg:
                cleaned = _pct_supported(leg[k], canon)
                if cleaned != leg[k]:
                    issues.append("unsupported probability removed from the trade plan")
                    leg[k] = cleaned
    claims, claim_issues = verify_claims(result.get("claims", []), {ev["evidence_id"]: ev for ev in evidence})
    result["claims"] = claims
    issues += [f"claim {i['claim']}: {i['from']} -> {i['to']} ({'; '.join(i['flags'])})" for i in claim_issues
               if i["from"] != i["to"]]
    result["execution"] = "analysis only - no order was placed or changed"
    return result, issues


def format_decision(r: dict, evidence: list[dict], issues: list[str], data_quality: dict) -> str:
    ev = {e["evidence_id"]: e for e in evidence}
    price = lambda v: f"{v:g}" if isinstance(v, (int, float)) else "—（无可靠价格，按条件执行）"
    lines = [
        f"**BIAS 倾向：{r['bias']}** — {r['conclusion']}", "",
        *(["**推理过程 REASONING**"] + [f"{i}. {s['step']}：{s['observation']} → {s['inference']}"
                                        for i, s in enumerate(r.get("reasoning_steps", []), 1)] + [""]
          if r.get("reasoning_steps") else []),
        "**MARKET CONTEXT 市场背景**", f"{r['market_context']}（Always In：{r['always_in']}）", "",
        "**PRIMARY SETUP 主要结构**", f"{r['primary_setup']['name']}（{r['primary_setup']['status']}）：{r['primary_setup']['description']}", "",
        "**ENTRY 入场**", f"{r['entry']['condition']}；价格：{price(r['entry']['price'])}", "",
        "**STOP 止损**", f"{r['stop']['logic']}；价格：{price(r['stop']['price'])}", "",
        "**TARGET 目标**", f"{r['target']['logic']}；价格：{price(r['target']['price'])}", "",
        "**RISK / REWARD 风险收益**",
        (f"{r['risk_reward']} : 1（由程序按入场/止损/目标计算）" if r.get("risk_reward") else "价格不足，无法计算；按止损逻辑控制风险"), "",
        "**INVALIDATION 失效条件**", r["invalidation"], "",
        "**ALTERNATIVE SCENARIO 备选情景**", r["alternative_scenario"], "",
    ]
    if r["bias"] == "WAIT" or r.get("wait_for"):
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
              "仅为分析建议，系统没有也不能下单。"]
    if r.get("follow_up_question"):
        lines += ["", f"🤔 **我想问你**：{r['follow_up_question']}"]
    lines += ["", "（可以继续追问或质疑任何一步，例如「为什么止损放在这里？」「我觉得这里该偏空」。）"]
    if issues:
        lines += ["", "🔎 校验：" + "；".join(issues)]
    if evidence:
        lines += ["", "**参考来源**", references_block(evidence)]
    return "\n".join(lines).strip()
