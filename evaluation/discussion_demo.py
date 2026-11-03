"""Multi-turn discussion demo with the real model (review, then questions and challenges).

    .venv\\Scripts\\python -m evaluation.discussion_demo evaluation\\samples\\mgc_5m_2026-11-02.webp [--model gpt-5.5]

Writes evaluation/runs/<timestamp>/discussion.md. config.toml is not changed.
"""

import argparse
import os
import shutil
import sys
import tomllib
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TURNS = [
    "你判断的整体逻辑是什么？请一步步说明。",
    "我觉得你右边判断错了，这里明明应该做多。",
    None,   # answer the coach's own follow-up question (filled in at run time)
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--note", default="MGC 5分钟")
    ap.add_argument("--model")
    args = ap.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    from main import load_env
    load_env()
    with open("config.toml", "rb") as f:
        cfg = tomllib.load(f)
    if args.model:
        cfg.update(review_model=args.model, study_model=args.model, decision_model=args.model)
    from agent.runtime import CoachAgent
    agent = CoachAgent(cfg, project_dir=ROOT)
    run_dir = os.path.join(ROOT, "evaluation", "runs", datetime.now().strftime("%Y%m%d_%H%M%S") + "_discussion")
    os.makedirs(run_dir)
    md = [f"# Discussion demo · model {agent.llm.model_for('review')}", ""]
    rv = agent.handle({"mode": "review", "image_path": args.image, "text": args.note, "note": args.note})
    if not rv["ok"]:
        raise SystemExit(rv["error"])
    shutil.copy(rv["image_path"], os.path.join(run_dir, "review.png"))
    md += ["## Review", "", "![review](review.png)", "", rv["text"], ""]
    sid, last_q = rv["session_id"], ""
    for i, q in enumerate(TURNS, 1):
        if q is None:
            if not last_q:
                break
            q = f"回答你的问题（{last_q}）：我看到右侧最后几根K线在下跌后有一根较长下影线，但没有强阳线跟进。"
        print(f"turn {i}: {q[:40]}")
        out = agent.handle({"text": q, "session_id": sid})
        md += [f"## Turn {i}", "", f"**你**：{q}", "", "**教练**：", "", out.get("text") or out.get("error", ""), ""]
        if out.get("revision"):
            shutil.copy(out["revision"]["image"], os.path.join(run_dir, os.path.basename(out["revision"]["image"])))
        last_q = out.get("follow_up_question", "")
    path = os.path.join(run_dir, "discussion.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("Report:", path)


if __name__ == "__main__":
    main()
