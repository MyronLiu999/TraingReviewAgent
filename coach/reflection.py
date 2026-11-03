"""Reflection: compare a saved (immutable) hypothesis with what happened afterwards.

The result is stored as a case outcome; lessons become CANDIDATE insights only. Nothing here
changes the original analysis, the user's confirmed profile, or the Brooks knowledge base.
"""

import json

REFLECT_SYSTEM = """\
You are reviewing one of your own earlier chart analyses against what happened afterwards.
The analysis below was written BEFORE the outcome; do not rewrite it, evaluate it. Be specific
and fair: one result proves little (trading is probabilistic) - separate a sound decision that
lost from a flawed reading. Check: market context, always-in direction, H/L counting, too much
weight on a local pattern, trend strength ignored, entry/stop logic. Lessons you propose are only
candidates for the user to approve. Write in Chinese with English terms.

# ORIGINAL ANALYSIS (immutable)
{hypothesis}

# WHAT HAPPENED (reported by {source})
{outcome}
"""

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "direction_moved": {"type": "string", "enum": ["as_expected", "opposite", "sideways", "unclear"]},
        "hypothesis_correct": {"type": "string", "enum": ["yes", "partly", "no", "unclear"]},
        "what_was_right": {"type": "string"},
        "what_was_wrong": {"type": "string"},
        "decision_quality": {"type": "string"},
        "lessons": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["reflection", "learning_difficulty", "procedural_rule"]},
                           "content": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["kind", "content", "rationale"], "additionalProperties": False}},
    },
    "required": ["direction_moved", "hypothesis_correct", "what_was_right", "what_was_wrong", "decision_quality",
                 "lessons"],
    "additionalProperties": False,
}


def build_system(case: dict, outcome: str, source: str) -> str:
    hyp = {k: case.get(k) for k in ("analysis_timestamp", "symbol", "timeframe", "mode", "market_context",
                                    "setup_classification", "initial_hypothesis")}
    return REFLECT_SYSTEM.format(hypothesis=json.dumps(hyp, ensure_ascii=False, indent=1)[:6000],
                                 outcome=outcome[:3000], source=source)


def format_reflection(r: dict, candidate_ids: list[int]) -> str:
    lines = [f"**结果对比**：方向 {r['direction_moved']} · 原判断 {r['hypothesis_correct']}", "",
             "**判断对的部分**", r["what_was_right"], "", "**判断错的部分**", r["what_was_wrong"], "",
             "**决策质量**", r["decision_quality"]]
    if r["lessons"]:
        lines += ["", "**候选经验**（只进入候选区，需你批准才会生效）"]
        lines += [f"- [候选 #{cid}] ({l['kind']}) {l['content']}" for cid, l in zip(candidate_ids, r["lessons"])]
    return "\n".join(lines)
