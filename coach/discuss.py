"""Ongoing discussion about one saved case (review or decision): follow-up questions, challenges,
"why?" - answered with the explicit chain of reasoning, and revised only when the evidence says so.

Every turn the model sees: the original analysis (immutable), the current view (with earlier
revisions), all marks with their reasons, the discussion so far, the question it asked the user
last time, the user's confirmed learning profile and freshly retrieved Brooks sources.
Revisions never overwrite: marks get new versions, judgments get case_revisions rows.
"""

import json

from chart_review import ANNOTATION_TYPES
from coach.study import LABEL_RULES
from knowledge.citations import CLAIMS_SCHEMA, format_claim, references_block, wrap_untrusted

DISCUSS_SYSTEM = """\
You are the user's Al Brooks price action coach, in an ONGOING conversation about one chart you
analysed earlier. The user may ask why, question a judgment, disagree, propose another reading,
or ask what you would do. Write in Chinese with the English terms.

How to answer:
1. `answer`: answer the question directly first, in 1-4 sentences.
2. `user_point`: if the user challenges or proposes something, judge it: agree / partly_agree /
   disagree, with the reason. Re-examine the chart honestly. Do NOT agree just because the user
   pushes back, and do not defend a reading the bars do not support. Being corrected is fine;
   being talked out of a correct reading is not. Use not_a_challenge if there is nothing to judge.
3. `reasoning_steps`: your logic as explicit steps. Each step = what you see on the chart (name
   the bars so the user can find them; say when something cannot be read on the screenshot) ->
   the Brooks principle you apply (cite source ids) -> what you infer from it.
4. `alternatives`: other readings you considered and why they are less likely (or equally likely).
5. `what_would_change_my_mind`: the concrete bars / prices / behaviour that would make you change it.
6. `confidence` + `confidence_reason`.
7. `revisions`: ONLY if you actually change a judgment now. For a mark use target "annotation"
   with its annotation_id and the corrected type/status/text/coordinates (grid 0-1000);
   new_status "withdrawn" removes it. For the overall reading use market_context / always_in
   (long/short/unclear) / current_state / decision_bias (LONG/SHORT/WAIT) / decision_plan with new_value.
   For fields you do not use, repeat the current values (or "" / "none" / 0). The original
   analysis is kept; revisions are stored as new versions.
8. `mark_explanation`: when a specific mark is in focus, fill all five fields; otherwise "".
9. `follow_up_question`: at most ONE question back to the user that moves the learning forward
   (make them look at a specific bar, or clarify what they mean). Empty if not useful.
   If your previous question is answered in this message, react to the user's answer.
10. `candidate_observations`: only if this exchange shows a recurring difficulty or preference
    (they stay candidates until the user approves them).
Only use what the chart shows up to its last bar; never assume what happened afterwards.
`claims` are YOUR supported statements; do not list the user's own statement as a claim (judge it in user_point).

{label_rules}
# THE CASE
Original analysis (written before this conversation, immutable):
{original}

Current view (after earlier revisions):
{view}

Marks on the chart (registry, current versions):
{marks}

# FOCUS
{focus}

# CONVERSATION ABOUT THIS CASE SO FAR (oldest first)
{history}

# YOUR LAST QUESTION TO THE USER
{pending}

# LEARNING PROFILE (confirmed by the user)
{learning}

# RETRIEVED SOURCES
{sources}
"""

TARGETS = ["annotation", "market_context", "always_in", "current_state", "decision_bias", "decision_plan"]
DISCUSS_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "user_point": {"type": "object", "properties": {
            "verdict": {"type": "string", "enum": ["agree", "partly_agree", "disagree", "not_a_challenge"]},
            "reason": {"type": "string"}}, "required": ["verdict", "reason"], "additionalProperties": False},
        "reasoning_steps": {"type": "array", "items": {"type": "object", "properties": {
            "observation": {"type": "string"}, "principle": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}}, "inference": {"type": "string"}},
            "required": ["observation", "principle", "evidence_ids", "inference"], "additionalProperties": False}},
        "mark_explanation": {"type": "object", "properties": {
            "meaning": {"type": "string"}, "why_here": {"type": "string"}, "supporting_bars": {"type": "string"},
            "why_not_alternatives": {"type": "string"}, "trading_significance": {"type": "string"}},
            "required": ["meaning", "why_here", "supporting_bars", "why_not_alternatives", "trading_significance"],
            "additionalProperties": False},
        "alternatives": {"type": "array", "items": {"type": "object", "properties": {
            "interpretation": {"type": "string"}, "why_less_likely": {"type": "string"}},
            "required": ["interpretation", "why_less_likely"], "additionalProperties": False}},
        "what_would_change_my_mind": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "confidence_reason": {"type": "string"},
        "claims": CLAIMS_SCHEMA,
        "revisions": {"type": "array", "items": {"type": "object", "properties": {
            "target": {"type": "string", "enum": TARGETS},
            "annotation_id": {"type": "string"},
            "new_value": {"type": "string"},
            "new_type": {"type": "string", "enum": ANNOTATION_TYPES + ["none"]},
            "new_status": {"type": "string", "enum": ["confirmed", "candidate", "uncertain", "withdrawn"]},
            "new_target_x": {"type": "number"}, "new_target_y": {"type": "number"},
            "reason": {"type": "string"}},
            "required": ["target", "annotation_id", "new_value", "new_type", "new_status", "new_target_x",
                         "new_target_y", "reason"], "additionalProperties": False}},
        "follow_up_question": {"type": "string"},
        "candidate_observations": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["learning_difficulty", "preference"]},
            "content": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["kind", "content", "rationale"], "additionalProperties": False}},
    },
    "required": ["answer", "user_point", "reasoning_steps", "mark_explanation", "alternatives",
                 "what_would_change_my_mind", "confidence", "confidence_reason", "claims", "revisions",
                 "follow_up_question", "candidate_observations"],
    "additionalProperties": False,
}

CHALLENGE_WORDS = ("错", "不对", "不是", "不应该", "不同意", "怀疑", "质疑", "为什么不", "难道", "应该是", "我觉得",
                   "我认为", "明明", "wrong", "disagree", "isn't", "not an", "why not", "重新")


def is_challenge(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in CHALLENGE_WORDS)


def _marks(annotations: list[dict]) -> str:
    rows = [{k: a.get(k) for k in ("label", "annotation_id", "type", "status", "text", "reason", "bar_ref", "version")}
            for a in annotations if a.get("kind") == "callout"]
    return json.dumps(rows, ensure_ascii=False, indent=0)[:5000] or "(no marks: decision case)"


def build_system(case: dict, view: dict, annotations: list[dict], focus: dict | None, history: list[dict],
                 pending: str, learning: dict, evidence: list[dict]) -> str:
    original = {k: case.get(k) for k in ("mode", "symbol", "timeframe", "analysis_timestamp", "market_context",
                                         "setup_classification", "initial_hypothesis", "data_quality", "note")}
    if focus:
        f = {k: focus.get(k) for k in ("label", "annotation_id", "type", "status", "text", "reason", "bar_ref",
                                       "coordinates", "snapped", "version", "change_reason")}
        focus_txt = ("The user is asking about this mark (image 2 is a zoomed crop of the original chart "
                     "around it, yellow circle):\n" + json.dumps(f, ensure_ascii=False, indent=1))
    else:
        focus_txt = "No single mark; the question is about the chart / the analysis as a whole."
    hist = "\n".join(f"[{h['role']}] {h['content'][:1500]}" for h in history[-12:]) or "(first question)"
    return DISCUSS_SYSTEM.format(
        label_rules=LABEL_RULES, original=json.dumps(original, ensure_ascii=False, indent=1)[:6000],
        view=json.dumps(view, ensure_ascii=False, indent=1), marks=_marks(annotations), focus=focus_txt,
        history=hist, pending=pending or "(none)", learning=json.dumps(learning, ensure_ascii=False)[:2000],
        sources=wrap_untrusted(evidence) if evidence else "(no sources retrieved)")


def check_steps(result: dict, evidence_ids: set) -> list[str]:
    """Removes evidence ids that were not retrieved (the model cannot cite what it was not given)."""
    issues = []
    for i, s in enumerate(result.get("reasoning_steps", [])):
        bad = [e for e in s["evidence_ids"] if e not in evidence_ids]
        if bad:
            issues.append(f"step {i + 1}: unknown source ids {bad} removed")
            s["evidence_ids"] = [e for e in s["evidence_ids"] if e in evidence_ids]
    return issues


VERDICT = {"agree": "同意", "partly_agree": "部分同意", "disagree": "不同意"}
CONF = {"high": "高", "medium": "中", "low": "低"}


def format_discussion(r: dict, evidence: list[dict], focus: dict | None, applied: list[str], issues: list[str]) -> str:
    ev = {e["evidence_id"]: e for e in evidence}
    out = []
    if focus:
        out += [f"**{focus.get('label', '')} {focus['annotation_id']}** · {focus.get('text', '')}"
                f"（{focus.get('status')} · v{focus.get('version', 1)}）", ""]
    out += ["**回答**", r["answer"].strip(), ""]
    up = r["user_point"]
    if up["verdict"] != "not_a_challenge":
        out += [f"**对你观点的判断：{VERDICT[up['verdict']]}**", up["reason"].strip(), ""]
    me = r["mark_explanation"]
    if focus and any(me.values()):
        out += ["**1. 含义**", me["meaning"], "", "**2. 为什么标在这里**", me["why_here"], "",
                "**3. 支持它的 K 线**", me["supporting_bars"], "", "**4. 为什么不是相似形态**", me["why_not_alternatives"], "",
                "**5. 交易意义**", me["trading_significance"], ""]
    if r["reasoning_steps"]:
        out.append("**我的推理过程**")
        for i, s in enumerate(r["reasoning_steps"], 1):
            refs = " ".join(ev[e]["citation"] for e in s["evidence_ids"] if e in ev)
            out.append(f"{i}. 观察：{s['observation']}\n   → 依据：{s['principle']}" + (f" {refs}" if refs else "")
                       + f"\n   → 推断：{s['inference']}")
        out.append("")
    if r["alternatives"]:
        out += ["**我考虑过的其他解释**"] + [f"- {a['interpretation']}：{a['why_less_likely']}" for a in r["alternatives"]] + [""]
    if r["what_would_change_my_mind"]:
        out += ["**什么情况会让我改变判断**", r["what_would_change_my_mind"], ""]
    out.append(f"**把握程度：{CONF[r['confidence']]}** — {r['confidence_reason']}")
    if r["claims"]:
        out += ["", "**6. 教材依据**" if focus else "**教材依据**"] + ["- " + format_claim(c, ev) for c in r["claims"]]
    if applied:
        out += ["", "**修改记录**（原判断保留，修改存为新版本）"] + [f"- {a}" for a in applied]
    if r.get("follow_up_question"):
        out += ["", f"🤔 **我想问你**：{r['follow_up_question']}"]
    if issues:
        out += ["", "🔎 校验：" + "；".join(issues)]
    if evidence:
        out += ["", "**参考来源**", references_block(evidence)]
    return "\n".join(out).strip()
