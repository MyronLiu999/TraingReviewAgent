"""End-to-end demo with the real model:
Upload Chart -> Retrieve Brooks Knowledge -> Analyze -> Annotate -> Explain -> Save Case -> Ask Follow-up.

    .venv\\Scripts\\python agent_cli.py demo evaluation\\samples\\mgc_5m_2026-11-02.webp

Writes evaluation/runs/<timestamp>/demo.md (+ images) and compares the review with the stored
legacy result for the same fixed sample (evaluation/samples/mgc_5m_legacy_result.json).
"""

import json
import os
import shutil
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def metrics(spec: dict) -> dict:
    callouts = spec.get("callouts", [])
    return {
        "callouts": len(callouts),
        "with_type": sum(1 for c in callouts if c.get("type")),
        "with_reason": sum(1 for c in callouts if c.get("reason")),
        "status": {s: sum(1 for c in callouts if c.get("status") == s) for s in ("confirmed", "candidate", "uncertain")},
        "snapped": sum(1 for c in callouts if c.get("snapped")),
        "has_always_in": bool(spec.get("always_in")),
        "has_right_edge_reading": bool(spec.get("current_state")),
        "has_limitations": bool(spec.get("limitations")),
        "annotation_ids": [c.get("annotation_id") for c in callouts if c.get("annotation_id")],
        "summary_wrapped": bool(spec.get("summary_lines")),
    }


def task_stats(agent, task_id: str) -> dict:
    t = agent.tasks.load(task_id)
    return {"state": t.state, "tools": [f"{c['tool']}({c.get('latency_s')}s,{'ok' if c.get('ok') else 'ERR'})" for c in t.tool_calls],
            "llm": [{k: c.get(k) for k in ("model", "schema", "latency_s", "prompt_tokens", "completion_tokens")}
                    for c in t.llm_calls],
            "retrieved": t.retrieved_doc_ids[:10], "memory_writes": t.memory_writes, "validation": t.validation[:8]}


def run_demo(agent, image: str, note: str) -> str:
    run_dir = os.path.join(HERE, "runs", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir)
    lines = [f"# End-to-end demo · {datetime.now():%Y-%m-%d %H:%M}", "", f"Chart: `{image}` · note: {note}",
             f"Knowledge: {agent.kb.stats()['version'] if agent.kb else 'UNAVAILABLE'} · model: {agent.llm.model_for('review')}", ""]

    print("1/4 Upload chart -> retrieve -> analyze -> annotate -> save case ...")
    rv = agent.handle({"mode": "review", "image_path": image, "text": note, "note": note, "source": "demo"})
    if not rv["ok"]:
        raise SystemExit(f"review failed: {rv['error']}")
    sid, case_id = rv["session_id"], rv["case_id"]
    shutil.copy(rv["image_path"], os.path.join(run_dir, "review.png"))
    lines += ["## 1. Review (annotated chart)", "", "![review](review.png)", "", rv["text"], "",
              "Task trace: `" + json.dumps(task_stats(agent, rv["task_id"]), ensure_ascii=False)[:1500] + "`", ""]

    marks = rv["annotations"]
    pick = next((a for a in marks if a["type"] in ("H1", "H2", "L1", "L2")), None) or (marks[0] if marks else None)
    if pick:
        q = f"你为什么把 {pick['label']} 标记为 {pick['type']}？"
        print(f"2/4 Explain {pick['label']} ({pick['annotation_id']}) ...")
        ex = agent.handle({"mode": "explain", "text": q, "session_id": sid})
        lines += [f"## 2. Explain: {q}", "", ex.get("text") or ex.get("error", ""), "",
                  "Task trace: `" + json.dumps(task_stats(agent, ex["task_id"]), ensure_ascii=False)[:1200] + "`", ""]

    print("3/4 Saved case record ...")
    case = agent.memory.case(case_id)
    lines += ["## 3. Saved case (episodic memory)", "", "```json",
              json.dumps({k: case[k] for k in ("case_id", "analysis_timestamp", "symbol", "timeframe", "mode",
                                               "setup_classification", "market_context", "data_quality")},
                         ensure_ascii=False, indent=1), "```", ""]

    follow = "这个结构在 Brooks 的书里是怎么定义的？我应该怎么判断它是否成立？"
    print("4/4 Follow-up study question ...")
    st = agent.handle({"mode": "study", "text": follow + (f"（指 {pick['text']}）" if pick else ""), "session_id": sid,
                       "depth": "brief"})
    lines += [f"## 4. Follow-up: {follow}", "", st.get("text") or st.get("error", ""), ""]

    legacy_path = os.path.join(HERE, "samples", "mgc_5m_legacy_result.json")
    if os.path.isfile(legacy_path) and "mgc_5m" in os.path.basename(image):
        legacy = json.load(open(legacy_path, encoding="utf-8"))
        new_spec = rv["spec"]
        lines += ["## 5. Old vs new on the fixed sample", "", "| metric | legacy | new |", "|---|---|---|"]
        a, b = metrics(legacy["spec"]), metrics(new_spec)
        for k in a:
            lines.append(f"| {k} | {a[k]} | {b[k]} |")
        lines += ["", f"Legacy summary: {legacy['spec'].get('summary', '')}", "",
                  f"New right-edge reading: {new_spec.get('current_state', '')} (always in: {new_spec.get('always_in')})", ""]

    report = os.path.join(run_dir, "demo.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {report}")
    return report
