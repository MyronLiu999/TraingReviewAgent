"""Command line for the Brooks coach (same engine as the web UI and Telegram).

    .venv\\Scripts\\python agent_cli.py study "请解释 H2 和两段式回调" [--depth brief|detailed|bar_by_bar|exercise]
    .venv\\Scripts\\python agent_cli.py review chart.png --note "MYM 5分钟"
    .venv\\Scripts\\python agent_cli.py explain "为什么 #3 是 H2？" [--case <case_id>] [--ann H2_001]
    .venv\\Scripts\\python agent_cli.py ask "我觉得右边已经是交易区间了"   (keep discussing the last chart / decision)
    .venv\\Scripts\\python agent_cli.py chat                             (interactive conversation; empty line quits)
    .venv\\Scripts\\python agent_cli.py decide chart.png "现在偏多还是偏空？" [--csv bars.csv --as-of "2026-11-02 14:30"]
    .venv\\Scripts\\python agent_cli.py outcome <case_id> "后来涨了 30 点"
    .venv\\Scripts\\python agent_cli.py memory list|approve ID|reject ID|delete ID|profile|rules|add-rule "..."|delete-profile ID|delete-rule ID|cases|audit
    .venv\\Scripts\\python agent_cli.py tasks [--id TASK_ID]          (observability: recent tasks / one task's trace)
    .venv\\Scripts\\python agent_cli.py demo chart.png               (end-to-end demo, see README)

The session is remembered in data/cli_session.txt so follow-up questions refer to the last chart.
"""

import argparse
import json
import logging
import os
import sys
import tomllib

ROOT = os.path.dirname(os.path.abspath(__file__))


def load():
    os.chdir(ROOT)
    from main import load_env
    load_env()
    with open("config.toml", "rb") as f:
        return tomllib.load(f)


def session_file(cfg) -> str:
    return os.path.join(ROOT, cfg.get("data_folder", "data"), "cli_session.txt")


def get_session(cfg):
    path = session_file(cfg)
    return open(path, encoding="utf-8").read().strip() if os.path.isfile(path) else None


def put_session(cfg, sid):
    path = session_file(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(sid or "")


def show(out: dict) -> None:
    if not out.get("ok", True):
        print(f"❌ {out.get('error')}  (task {out.get('task_id')})")
        sys.exit(1)
    print(out.get("text", ""))
    extra = {k: out[k] for k in ("mode", "task_id", "case_id", "image_path", "candidate_id") if out.get(k)}
    if out.get("revision"):
        extra["revision"] = out["revision"]
    print("\n" + json.dumps(extra, ensure_ascii=False))


def memory_cmd(agent, args) -> None:
    mem, act, arg = agent.memory, args.action, args.arg
    if act == "list":
        for c in mem.candidates("candidate"):
            print(f"#{c['id']} [{c['kind']}] {c['content']}\n     source={c['source_type']} {c['source_ref']} · {c['rationale']}")
        print("(approve with: agent_cli.py memory approve ID)")
    elif act == "approve":
        print("→", mem.approve_candidate(int(arg), approved_by=os.environ.get("USERNAME", "cli-user")))
    elif act == "reject":
        mem.reject_candidate(int(arg), "rejected in CLI"); print("rejected")
    elif act == "delete":
        mem.delete_candidate(int(arg)); print("deleted")
    elif act == "profile":
        for p in mem.profile():
            print(f"{p['id']}. [{p['category']}] {p['content']}  (source {p['source_type']} {p['source_ref']}, by {p['confirmed_by']})")
    elif act == "delete-profile":
        mem.delete_profile(int(arg)); print("deleted")
    elif act == "rules":
        for r in mem.rules():
            print(f"{r['id']}. {r['rule']}  (approved by {r['approved_by']})")
    elif act == "add-rule":
        print("rule", mem.add_rule(arg, approved_by=os.environ.get("USERNAME", "cli-user")))
    elif act == "delete-rule":
        mem.delete_rule(int(arg)); print("deleted")
    elif act == "cases":
        for c in mem.cases(30):
            print(f"{c['case_id']}  {c['mode']:8} {c['symbol'] or '-':6} {c['setup_classification'] or ''}")
    elif act == "audit":
        for a in mem.audit_log(40):
            print(a["ts"], a["action"], a["target"], a["detail"][:120])


def main() -> None:
    ap = argparse.ArgumentParser(prog="agent_cli.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("study"); p.add_argument("question"); p.add_argument("--depth", default="detailed")
    p = sub.add_parser("review"); p.add_argument("image"); p.add_argument("--note", default="")
    p = sub.add_parser("explain"); p.add_argument("question"); p.add_argument("--case"); p.add_argument("--ann")
    p = sub.add_parser("decide"); p.add_argument("image", nargs="?"); p.add_argument("question", nargs="?",
                                                                                   default="现在我应该更关注做多还是做空？")
    p.add_argument("--csv"); p.add_argument("--as-of")
    p = sub.add_parser("ask"); p.add_argument("question"); p.add_argument("--case")
    sub.add_parser("chat")
    p = sub.add_parser("outcome"); p.add_argument("case_id"); p.add_argument("text")
    p = sub.add_parser("memory"); p.add_argument("action"); p.add_argument("arg", nargs="?")
    p = sub.add_parser("tasks"); p.add_argument("--id")
    p = sub.add_parser("demo"); p.add_argument("image")
    p.add_argument("--note", default="MGC 5分钟")
    p.add_argument("--model", help="try another model for this run only (config.toml is not changed)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load()
    if getattr(args, "model", None):
        cfg.update(review_model=args.model, study_model=args.model, decision_model=args.model)
    from agent.runtime import CoachAgent
    agent = CoachAgent(cfg, project_dir=ROOT)
    sid = get_session(cfg)

    def run(req):
        out = agent.handle({**req, "session_id": sid, "source": "cli"})
        put_session(cfg, out.get("session_id"))
        return out

    if args.cmd == "study":
        show(run({"mode": "study", "text": args.question, "depth": args.depth}))
    elif args.cmd == "review":
        show(run({"mode": "review", "image_path": args.image, "text": args.note, "note": args.note}))
    elif args.cmd == "explain":
        show(run({"mode": "explain", "text": args.question, **({"case_id": args.case} if args.case else {}),
                  **({"annotation_id": args.ann} if args.ann else {})}))
    elif args.cmd == "decide":
        req = {"mode": "decision", "text": args.question}
        if args.image:
            req["image_path"] = args.image
        if args.csv:
            req["csv"] = args.csv
        if args.as_of:
            req["as_of"] = args.as_of
        show(run(req))
    elif args.cmd == "ask":
        show(run({"text": args.question, **({"case_id": args.case} if args.case else {})}))
    elif args.cmd == "chat":
        print("和教练对话（直接回车退出）。有图在讨论时会接着讨论那张图。")
        while True:
            try:
                q = input("\n你> ").strip()
            except EOFError:
                break
            if not q:
                break
            out = run({"text": q})
            sid = out.get("session_id") or sid
            print("\n教练>", out.get("text") if out.get("ok") else f"❌ {out.get('error')}")
    elif args.cmd == "outcome":
        show(run({"mode": "outcome", "case_id": args.case_id, "text": args.text}))
    elif args.cmd == "memory":
        memory_cmd(agent, args)
    elif args.cmd == "tasks":
        if args.id:
            t = agent.tasks.load(args.id)
            print(json.dumps({k: v for k, v in t.__dict__.items() if k != "data"}, ensure_ascii=False, indent=1, default=str))
            for e in agent.tasks.events(args.id):
                print(*e)
        else:
            for t in agent.tasks.recent(30):
                print(t["created"][:19], t["mode"], t["state"], t["task_id"])
    elif args.cmd == "demo":
        from evaluation.demo import run_demo
        run_demo(agent, args.image, args.note)


if __name__ == "__main__":
    main()
