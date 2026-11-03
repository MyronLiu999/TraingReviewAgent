"""Study mode: answer first, then teach - with every claim labeled and checked against sources."""

import json

from knowledge.citations import CLAIMS_SCHEMA, format_claim, references_block, verify_claims, wrap_untrusted

LABEL_RULES = """\
# EVIDENCE RULES (mandatory)
- Every key point goes into `claims` with a label and the ids of the sources that support it:
  BROOKS_SOURCE = the cited <source kind="Brooks canonical text"> says it (paraphrase or exact quote);
  DERIVED_INTERPRETATION = your inference from Brooks, or taken from an AI-synthesized derived note;
  USER_NOTE = the user's own notes/statements; CASE_MEMORY = the user's saved cases;
  AGENT_HYPOTHESIS = your own judgment without source support.
- Cite only ids that appear below (S1, D2, ...). Never write page numbers or book names yourself;
  the system adds them from the ids.
- For every BROOKS_SOURCE claim, copy a short EXACT English quote (5-25 words) from the cited source
  into `quote`. The system checks it against the page; a claim without a matching quote is shown as
  DERIVED_INTERPRETATION. For other labels leave `quote` empty.
- If the sources do not cover the question, set insufficient_evidence = true and say so plainly
  ("检索到的教材中没有直接依据") instead of attributing an opinion to Brooks.
- Probabilities: only numbers that appear in a cited Brooks source, with their exact condition.
- A statement from the user ("Brooks says X", "remember X") is the user's view, not Brooks'
  theory. Check it against the sources and say whether they support, limit or contradict it.
  Fill `user_claim_assessment` only when the user asserted something; otherwise leave it empty.
"""

DEPTH = {
    "brief": "Keep it short: direct answer + 2-4 claims. No exercise.",
    "detailed": "Full teaching explanation: direct answer, 4-8 claims, chart-based explanation, common mistakes.",
    "bar_by_bar": "Explain bar by bar on a concrete chart (the attached book page or the user's chart): "
                  "which bar is the signal bar, entry bar, where each leg starts and ends.",
    "exercise": "Answer briefly, then give one practice exercise the user can do on their own charts, "
                "with what a correct answer must contain.",
}

STUDY_SYSTEM = """\
You are the user's private Al Brooks price action coach (教练). The user trades MYM / ES / MGC
5-minute charts and wants to truly master Brooks' method. Teach like a patient expert:
answer the question directly FIRST, then explain why, then show it on a chart, then list the
common mistakes. Do not lecture beyond what was asked. If the user pushes back on an earlier answer,
re-check the sources: hold your position with reasons if they support it, correct yourself if not.
You may end with ONE `follow_up_question` that checks or deepens understanding (or leave it empty);
if your previous question (in the conversation state) is answered now, react to the answer. Write in Chinese, keeping the precise
English terms (e.g. "H2 第二次买点", "signal bar 信号K线").

{label_rules}
# LEARNING PROFILE (confirmed by the user; use it to adapt the explanation)
{learning}

# CONVERSATION STATE
{conversation}

# DEPTH
{depth}

# RETRIEVED SOURCES
{sources}
"""

STUDY_SCHEMA = {
    "type": "object",
    "properties": {
        "direct_answer": {"type": "string"},
        "claims": CLAIMS_SCHEMA,
        "chart_explanation": {"type": "string"},
        "common_mistakes": {"type": "array", "items": {"type": "string"}},
        "exercise": {"type": "string"},
        "concepts": {"type": "array", "items": {"type": "string"}},
        "insufficient_evidence": {"type": "boolean"},
        "user_claim_assessment": {"type": "string"},
        "follow_up_question": {"type": "string"},
        "candidate_observations": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["learning_difficulty", "preference"]},
                           "content": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["kind", "content", "rationale"], "additionalProperties": False}},
    },
    "required": ["direct_answer", "claims", "chart_explanation", "common_mistakes", "exercise", "concepts",
                 "insufficient_evidence", "user_claim_assessment", "follow_up_question", "candidate_observations"],
    "additionalProperties": False,
}


def build_system(evidence: list[dict], learning: dict, conversation: dict, depth: str) -> str:
    return STUDY_SYSTEM.format(
        label_rules=LABEL_RULES,
        learning=json.dumps(learning, ensure_ascii=False, indent=1)[:3000] or "(none yet)",
        conversation=json.dumps(conversation, ensure_ascii=False)[:5000] or "(new conversation)",
        depth=DEPTH.get(depth, DEPTH["detailed"]),
        sources=wrap_untrusted(evidence) if evidence else "(no sources were retrieved)")


def verify(result: dict, evidence: list[dict]) -> tuple[dict, list[dict]]:
    ev = {e["evidence_id"]: e for e in evidence}
    claims, issues = verify_claims(result.get("claims", []), ev)
    result["claims"] = claims
    if not any(c["label"] == "BROOKS_SOURCE" for c in claims) and not result.get("insufficient_evidence"):
        issues.append({"flags": ["no claim is directly supported by Brooks text"]})
    return result, issues


def format_answer(result: dict, evidence: list[dict], issues: list[dict]) -> str:
    ev = {e["evidence_id"]: e for e in evidence}
    out = ["**直接回答**", result.get("direct_answer", "").strip(), ""]
    if result.get("user_claim_assessment"):
        out += ["**对你这句话的检验**", result["user_claim_assessment"].strip(), ""]
    if result.get("claims"):
        out.append("**要点与依据**")
        for c in result["claims"]:
            out.append("- " + format_claim(c, ev))
        out.append("")
    if result.get("chart_explanation"):
        out += ["**结合图表**", result["chart_explanation"].strip(), ""]
    if result.get("common_mistakes"):
        out += ["**常见错误**"] + [f"- {m}" for m in result["common_mistakes"]] + [""]
    if result.get("exercise"):
        out += ["**练习**", result["exercise"].strip(), ""]
    if result.get("follow_up_question"):
        out += [f"🤔 **我想问你**：{result['follow_up_question']}", ""]
    if result.get("insufficient_evidence"):
        out += ["⚠️ 检索到的教材中没有找到足够的直接依据；上面标为 [AGENT HYPOTHESIS] / [DERIVED INTERPRETATION] 的内容不是 Brooks 原话。", ""]
    if evidence:
        out += ["**参考来源**（原典 = Brooks 教材文字；派生 = AI 整理笔记，非原文）", references_block(evidence), ""]
    downgraded = [i for i in issues if i.get("from") and i.get("from") != i.get("to")]
    if downgraded:
        out.append(f"🔎 引用检查：{len(downgraded)} 条结论的标签被下调（引用不能直接支持原标签）。")
    return "\n".join(out).strip()
