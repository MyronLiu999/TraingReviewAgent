"""CoachAgent: the one entry point for Web, Telegram and CLI.

    agent = CoachAgent(cfg)
    agent.handle({"text": "请解释 H2", "session_id": sid})                 -> study
    agent.handle({"image": pil_image, "text": "MYM 5分钟"})                -> review (annotated chart)
    agent.handle({"text": "为什么 #3 是 H2？", "session_id": sid})          -> explain the mark
    agent.handle({"image": img, "text": "现在偏多还是偏空？"})              -> decision (LONG/SHORT/WAIT)
    agent.handle({"text": "以后请记住，所有 H2 都是高概率买点"})            -> memory (candidate only)
    agent.handle({"mode": "outcome", "case_id": cid, "text": "后来涨了20点"}) -> reflection

Each call runs a harness Task (states, checkpoints, tool calls, model calls and validation are
recorded in data/agent.db and data/traces). Brooks knowledge is read through CanonicalKB, which
has no write path; memory lives in data/memory.db.
"""

import contextvars
import io
import json
import logging
import os
import re
from datetime import datetime

from PIL import Image
from pydantic import BaseModel, Field

import chart_review as cr
from agent.harness import Harness, Task, TaskStore, Tool, ToolRegistry, Tracer
from agent.router import find_annotation_ref, route
from coach import decision as dec
from coach import discuss as disc
from coach import plan_chart
from coach import reflection as refl
from coach import study
from coach.discuss import is_challenge as exp_is_challenge
from knowledge.manifest import is_inside
from memory.store import MemoryStore

log = logging.getLogger(__name__)
DEFAULT_TRADE_QUESTION = "现在这里能不能做？如果做，在哪里入场、止损放哪、目标在哪？"
CURRENT_TASK: contextvars.ContextVar = contextvars.ContextVar("current_task", default=None)

CONCEPT_PATTERNS = {
    "high 2": r"\bH2\b|high 2", "high 1": r"\bH1\b|high 1", "low 2": r"\bL2\b|low 2", "low 1": r"\bL1\b|low 1",
    "two-legged pullback": r"两段|两腿|two[- ]leg", "signal bar": r"信号\s*K线|signal bar", "breakout": r"突破|breakout",
    "trading range": r"交易区间|trading range", "always in": r"always in|始终在场|多空主导",
    "wedge": r"楔形|wedge", "major trend reversal": r"\bMTR\b|主要趋势反转", "final flag": r"最终旗形|final flag",
    "double top/bottom": r"双顶|双底|double (top|bottom)", "climax": r"高潮|climax", "trend line": r"趋势线|trend ?line",
    "measured move": r"等距|measured move", "failed breakout": r"失败突破|failed breakout", "stop placement": r"止损|stop",
}


def detect_concepts(text: str) -> list[str]:
    return [c for c, pat in CONCEPT_PATTERNS.items() if re.search(pat, text or "", re.I)]


def _strip(evidence: list[dict]) -> list[dict]:
    return [{k: v for k, v in e.items() if k != "full_text"} for e in evidence]


# ---------------------------------------------------------------- tool input models

class RetrieveIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    k_canonical: int = 6
    k_derived: int = 4
    extra_terms: list[str] = []


class MarketDataIn(BaseModel):
    csv: str
    as_of: str | None = None


class AnalyzeChartIn(BaseModel):
    image_path: str
    note: str = ""
    extra_knowledge: str = ""


class AnnotateIn(BaseModel):
    image_path: str
    spec: dict
    out_path: str
    original_size: list[int] = []
    snap: bool = True
    layout: bool = True


class CasesIn(BaseModel):
    text: str = ""
    limit: int = 5


class EmptyIn(BaseModel):
    pass


class SaveCaseIn(BaseModel):
    case: dict
    annotations: list[dict] = []


class CandidateIn(BaseModel):
    kind: str
    content: str
    rationale: str = ""
    source_type: str = "agent"
    source_ref: str = ""


class EvaluateIn(BaseModel):
    system: str
    question: str
    image_path: str | None = None


class DiscussIn(BaseModel):
    system: str
    question: str
    image_paths: list[str] = []


class CoachAgent:
    def __init__(self, cfg: dict, llm=None, project_dir: str = ".", kb=None, memory: MemoryStore | None = None):
        self.cfg = cfg
        self.project_dir = os.path.abspath(project_dir)
        self.data_dir = os.path.join(self.project_dir, cfg.get("data_folder", "data"))
        self.canonical_root = cfg.get("canonical_store") or None
        if self.canonical_root and is_inside(self.data_dir, self.canonical_root):
            raise PermissionError("data_folder must not be inside canonical_store")
        os.makedirs(self.data_dir, exist_ok=True)
        self.reviews_dir = os.path.join(self.project_dir, cr.REVIEWS_DIR)
        self.kb = kb if kb is not None else self._open_kb()
        self.memory = memory or MemoryStore(os.path.join(self.data_dir, "memory.db"), self.canonical_root)
        if llm is None:
            from agent.llm import LLM
            llm = LLM(cfg)
        self.llm = llm
        self.tracer = Tracer(os.path.join(self.data_dir, "traces"))
        self.tasks = TaskStore(os.path.join(self.data_dir, "agent.db"))
        self.tools = ToolRegistry()
        self._register_tools()
        self.harness = Harness(self.tasks, self.tools, self.tracer)

    def close(self) -> None:
        """Closes the SQLite connections (needed before deleting the data folder on Windows)."""
        for obj in (self.kb, self.memory, self.tasks):
            if obj is not None:
                obj.close()

    def _open_kb(self):
        if not self.canonical_root:
            log.warning("canonical_store not configured: answers will have no Brooks citations")
            return None
        try:
            from knowledge.store import CanonicalKB
            return CanonicalKB(self.canonical_root)
        except Exception as e:  # missing/broken store must not take the review app down
            log.error("Brooks canonical knowledge unavailable: %s", e)
            return None

    # ------------------------------------------------------------ tools
    def _register_tools(self) -> None:
        t = self.tools.register
        t(Tool("retrieve_brooks_knowledge", "Search the read-only Brooks canonical KB and derived notes; "
               "returns evidence with ids and code-built citations.", self._t_retrieve, RetrieveIn,
               permission="read", timeout_s=20))
        t(Tool("get_market_data", "Load OHLC bars from CSV, drop bars after as_of, compute swing/EMA/H-L counts.",
               self._t_market, MarketDataIn, permission="read", timeout_s=20))
        t(Tool("analyze_chart_structure", "Vision model reads the chart and proposes annotations (+ refine pass).",
               self._t_analyze_chart, AnalyzeChartIn, permission="llm", timeout_s=600, retries=1))
        t(Tool("annotate_chart", "Snap, register (ids) and draw the annotations on the original screenshot.",
               self._t_annotate, AnnotateIn, permission="render", timeout_s=60))
        t(Tool("explain_annotation", "Model explains, defends or revises the analysis of a saved case "
               "(one mark or the whole chart) with its reasoning chain.",
               self._t_explain, DiscussIn, permission="llm", timeout_s=400, retries=1, log_args=False))
        t(Tool("search_similar_cases", "Find saved cases by setup, context or symbol.", self._t_cases, CasesIn,
               permission="read", timeout_s=10))
        t(Tool("read_learning_memory", "Confirmed profile, approved rules and concept counters.",
               self._t_learning, EmptyIn, permission="read", timeout_s=10))
        t(Tool("save_review_case", "Save an episodic case (immutable hypothesis) and its annotation registry.",
               self._t_save_case, SaveCaseIn, permission="case_write", timeout_s=20))
        t(Tool("evaluate_trade_hypothesis", "Decision model: LONG/SHORT/WAIT plan (validated afterwards).",
               self._t_evaluate, EvaluateIn, permission="llm", timeout_s=300, retries=1, log_args=False))
        t(Tool("record_candidate_insight", "Store a candidate insight (never trusted until a human approves).",
               self._t_candidate, CandidateIn, permission="memory_write", timeout_s=10))

    def _t_retrieve(self, query, k_canonical, k_derived, extra_terms):
        if self.kb is None:
            return {"evidence": [], "available": False}
        ev = self.kb.search(query, k_canonical, k_derived, int(self.cfg.get("rag_char_budget", 16000)), extra_terms)
        return {"evidence": [{**e.to_dict(), "full_text": e.full_text} for e in ev], "available": True}

    def _t_market(self, csv, as_of):
        from market import ohlc
        bars = ohlc.load_csv(csv)
        cut, dropped = ohlc.cut_at(bars, ohlc.parse_time(as_of) if as_of else None)
        return {"summary": ohlc.summarize(cut), "dropped_future_bars": dropped,
                "bars": [[b.time.isoformat(), b.open, b.high, b.low, b.close] for b in cut]}

    def _t_analyze_chart(self, image_path, note, extra_knowledge):
        chart = Image.open(image_path)
        reviewer = cr.ChartReviewer(self.cfg, llm=self.llm, extra_knowledge=extra_knowledge)
        return reviewer.analyze(chart, note, recorder=self._recorder)

    def _t_annotate(self, image_path, spec, out_path, original_size, snap, layout):
        chart = Image.open(image_path).convert("RGB")
        spec = cr.finalize_spec(chart, spec, snap=snap, original_size=tuple(original_size) or chart.size,
                                layout=layout)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        cr.render(chart, spec).save(out_path)
        return {"spec": spec, "image_path": out_path}

    def _t_explain(self, system, question, image_paths):
        content: list = [{"type": "text", "text": f"User: {question}"}]
        names = ["the annotated chart (current version)", "zoomed crop of the ORIGINAL chart around the mark"]
        for i, path in enumerate(image_paths):
            content += [{"type": "text", "text": f"Image {i + 1}: {names[min(i, 1)]}"},
                        {"type": "image_url", "image_url": {"url": cr.to_data_url(Image.open(path)), "detail": "high"}}]
        return self.llm.json_call(system=system, content=content, schema_name="case_discussion",
                                  schema=disc.DISCUSS_SCHEMA, purpose="study", max_tokens=6000, recorder=self._recorder)

    def _t_cases(self, text, limit):
        return {"cases": self.memory.search_cases(text, limit) if text else self.memory.cases(limit)}

    def _t_learning(self):
        return self.memory.learning_context()

    def _t_save_case(self, case, annotations):
        cid = self.memory.save_case(case)
        if annotations:
            self.memory.save_annotations(cid, annotations)
        return {"case_id": cid}

    def _t_evaluate(self, system, question, image_path):
        content: list = [{"type": "text", "text": question}]
        if image_path:
            content.append({"type": "image_url", "image_url": {
                "url": cr.to_data_url(cr.with_grid(cr.prepare(Image.open(image_path)))), "detail": "high"}})
        return self.llm.json_call(system=system, content=content, schema_name="trade_decision",
                                  schema=dec.DECISION_SCHEMA, purpose="decision", max_tokens=8000,
                                  recorder=self._recorder)

    def _t_candidate(self, kind, content, rationale, source_type, source_ref):
        return {"candidate_id": self.memory.add_candidate(kind, content, rationale, source_type, source_ref)}

    @property
    def _current_task(self) -> Task | None:
        return CURRENT_TASK.get()

    def _recorder(self, entry: dict) -> None:
        task = CURRENT_TASK.get()
        if task is not None:
            task.record_llm(entry)

    # ------------------------------------------------------------ entry point
    def handle(self, request: dict) -> dict:
        sess = self.memory.session(request.get("session_id"), request.get("source", "web"))
        sid = sess["session_id"]
        mode = route(request, sess["state"])
        handler = {"study": self.study, "review": self.review, "decision": self.decide, "explain": self.explain,
                   "discuss": self.discuss, "memory": self.remember, "outcome": self.record_outcome}[mode]
        token = CURRENT_TASK.set(None)
        try:
            result = handler(request, sid)
        except Exception as e:
            log.exception("%s failed", mode)
            return {"ok": False, "mode": mode, "session_id": sid, "error": f"{type(e).__name__}: {e}",
                    "task_id": getattr(CURRENT_TASK.get(), "task_id", None)}
        finally:
            CURRENT_TASK.reset(token)
        return {"ok": True, "mode": mode, "session_id": sid, **result}

    def _task(self, mode: str, request: dict, sid: str) -> Task:
        inputs = {k: v for k, v in request.items() if k not in ("image",)}
        task = self.harness.new_task(mode, inputs, sid)
        CURRENT_TASK.set(task)
        return task

    def _image_from(self, request: dict) -> Image.Image | None:
        img = request.get("image")
        if img is None and request.get("image_path"):
            img = Image.open(request["image_path"])
        if isinstance(img, (bytes, bytearray)):
            img = Image.open(io.BytesIO(img))
        if img is not None:
            img.load()
        return img

    def _retrieve(self, task: Task, query: str, **kw) -> list[dict]:
        out = self.harness.tool(task, "retrieve_brooks_knowledge", query=query[:4000],
                                k_canonical=int(kw.get("k_canonical", self.cfg.get("rag_top_canonical", 6))),
                                k_derived=int(kw.get("k_derived", self.cfg.get("rag_top_derived", 4))),
                                extra_terms=kw.get("extra_terms", []))
        ev = out["evidence"]
        task.retrieved_doc_ids += [e["doc_id"] for e in ev]
        if not out.get("available", True):
            task.validation.append({"warning": "Brooks canonical knowledge unavailable"})
        return ev

    # ------------------------------------------------------------ STUDY
    def study(self, request: dict, sid: str, user_claim: str | None = None) -> dict:
        question = (request.get("text") or "").strip()
        depth = request.get("depth", "detailed")
        task = self._task("study", request, sid)
        figures: list[str] = []

        def validate(t):
            if not question:
                raise ValueError("Empty question")
            if len(question) > 4000:
                raise ValueError("Question too long (max 4000 characters)")
            t.data["conversation"] = self.memory.context_for(sid)

        def retrieve(t):
            pinned = t.data["conversation"].get("pinned", {})
            q = question
            if pinned.get("last_concepts") and len(question) < 40:   # short follow-ups keep the topic
                q += " " + " ".join(pinned["last_concepts"])
            t.data["evidence"] = self._retrieve(t, q)
            t.data["learning"] = self.harness.tool(t, "read_learning_memory")
            n = int(self.cfg.get("study_attach_figures", 1))
            if n and self.kb is not None:
                for e in t.data["evidence"]:
                    if len(figures) >= n:
                        break
                    if e["authority"] == "canonical" and e["figures"]:
                        try:
                            path = self.kb.render_figure(e["source_id"], e["page"], os.path.join(self.data_dir, "figure_cache"))
                        except Exception as err:  # figure rendering is a bonus, never fatal
                            log.warning("figure render failed: %s", err)
                            path = None
                        if path:
                            figures.append(path)
                            t.data.setdefault("figures", []).append({"evidence_id": e["evidence_id"], "path": path})

        def analyze(t):
            system = study.build_system(t.data["evidence"], t.data["learning"], t.data["conversation"], depth)
            content: list = [{"type": "text", "text": question if not user_claim else
                              f"The user said: \"{user_claim}\" and asked me to remember it as a rule. Check this "
                              f"claim against the sources (fill user_claim_assessment); do not adopt it."}]
            for f in t.data.get("figures", []):
                content += [{"type": "text", "text": f"Original book page for {f['evidence_id']} (compressed scan; "
                                                     "describe only details you can actually see)."},
                            {"type": "image_url", "image_url": {"url": cr.to_data_url(Image.open(f["path"])), "detail": "high"}}]
            t.data["raw"] = self.llm.json_call(system=system, content=content, schema_name="study_answer",
                                               schema=study.STUDY_SCHEMA, purpose="study", max_tokens=6000,
                                               recorder=t.record_llm)

        def verify(t):
            result, issues = study.verify(t.data["raw"], t.data["evidence"])
            t.data["result"], t.validation = result, t.validation + issues
            for obs in result.get("candidate_observations", [])[:2]:
                out = self.harness.tool(t, "record_candidate_insight", kind=obs["kind"], content=obs["content"],
                                        rationale=obs["rationale"], source_type="agent", source_ref=f"task:{t.task_id}")
                t.memory_writes.append(f"candidate:{out['candidate_id']}")

        def save(t):
            concepts = list(dict.fromkeys(detect_concepts(question) + [c.lower() for c in t.data["result"].get("concepts", [])]))
            self.memory.note_concepts(concepts)
            t.data["text"] = study.format_answer(t.data["result"], t.data["evidence"], t.validation)
            self.memory.add_message(sid, "user", question, "study")
            self.memory.add_message(sid, "assistant", t.data["text"], "study", [e["doc_id"] for e in t.data["evidence"]])
            self.memory.update_session(sid, last_mode="study", last_question=question, last_concepts=concepts[:5],
                                       pending_question=t.data["result"].get("follow_up_question", ""),
                                       pending_case_id=None)
            t.memory_writes += ["messages", "concept_stats"]

        self.harness.run(task, [("validate", "VALIDATING", validate), ("retrieve", "RETRIEVING", retrieve),
                                ("analyze", "ANALYZING", analyze), ("verify", "VERIFYING", verify),
                                ("save", "SAVING", save)])
        return {"task_id": task.task_id, "text": task.data["text"], "evidence": _strip(task.data["evidence"]),
                "claims": task.data["result"]["claims"], "validation": task.validation,
                "figures": task.data.get("figures", [])}

    # ------------------------------------------------------------ MEMORY ("remember this")
    def remember(self, request: dict, sid: str) -> dict:
        text = (request.get("text") or "").strip()
        # store what to remember, not the request wording ("以后请记住，" / "请记住：")
        text = re.sub(r"^(以后|今后|从现在起|从今往后)?[，,\s]*(请)?(记住|记得|记下来)[：:，,\s]*", "", text) or text
        concepts = detect_concepts(text)
        kind = "user_claim" if concepts else "preference"
        cid = self.memory.add_candidate(kind, text, "The user asked the coach to remember this. Pending human approval.",
                                        source_type="user", source_ref=f"session:{sid}")
        notice = (f"📝 已记录为**候选**（#{cid}，类型 {kind}），没有写入 Brooks 原典，也没有成为你的正式规则。"
                  "需要你在「记忆」页面或用 `python agent_cli.py memory approve "
                  f"{cid}` 明确批准后，才会作为[USER NOTE]进入你的个人记录。")
        if kind != "user_claim":
            return {"text": notice, "candidate_id": cid}
        out = self.study({**request, "depth": "brief"}, sid, user_claim=text)
        return {**out, "text": notice + "\n\n" + out["text"], "candidate_id": cid}

    # ------------------------------------------------------------ REVIEW
    def review(self, request: dict, sid: str) -> dict:
        image = self._image_from(request)
        task = self._task("review", request, sid)
        rid = cr.new_review_id()
        task.data.update(case_id=rid, folder=os.path.join(self.reviews_dir, rid), sid=sid,
                         note=(request.get("note") or request.get("text") or "").strip()[:500],
                         source=request.get("source", "web"))

        def validate(t):
            if image is None:
                raise ValueError("No chart image")
            os.makedirs(t.data["folder"], exist_ok=True)
            path = os.path.join(t.data["folder"], "original.png")
            cr.prepare(image).save(path)
            t.data.update(input_image=path, original_size=list(image.size),
                          analysis_timestamp=datetime.now().isoformat(timespec="seconds"))
            t.artifacts.append(path)

        self.harness.run(task, [("validate", "VALIDATING", validate)] + self._review_steps())
        meta = {k: v for k, v in task.data["meta"].items() if k != "spec"}
        return {"task_id": task.task_id, "case_id": rid, "text": task.data["text"], "meta": meta,
                "image_path": task.data["review_image"], "annotations": meta["annotations"],
                "validation": task.validation, "spec": task.data["spec"]}

    def _review_steps(self) -> list:
        """Review steps after VALIDATING. They read everything from task.data (checkpointed),
        so a failed review can be resumed from the last finished step."""

        def retrieve(t):
            learning = self.harness.tool(t, "read_learning_memory")
            similar = self.harness.tool(t, "search_similar_cases", text=_symbol(t.data["note"]) or "", limit=3)["cases"]
            extra = []
            if learning["approved_rules"]:
                extra.append("# USER'S APPROVED ANALYSIS RULES ([USER NOTE])\n" + "\n".join(f"- {r}" for r in learning["approved_rules"]))
            prefs = [p["content"] for p in learning["confirmed_profile"] if p["category"] in ("preference", "learning_difficulty")]
            if prefs:
                extra.append("# USER PREFERENCES / KNOWN DIFFICULTIES (confirmed)\n" + "\n".join(f"- {p}" for p in prefs))
            t.data["extra_knowledge"] = "\n".join(extra)
            t.data["similar_cases"] = similar
            if self.kb is not None:
                t.retrieved_doc_ids.append(f"derived:{self.cfg.get('knowledge_primer', '11_REVIEW_AGENT_PRIMER.md')}@{self.kb.version}")

        def analyze(t):
            t.data["spec_raw"] = self.harness.tool(t, "analyze_chart_structure", image_path=t.data["input_image"],
                                                   note=t.data["note"], extra_knowledge=t.data["extra_knowledge"])

        def verify(t):
            spec, issues = validate_review_spec(t.data["spec_raw"])
            t.data["spec_checked"], t.validation = spec, t.validation + issues

        def render(t):
            out = self.harness.tool(t, "annotate_chart", image_path=t.data["input_image"], spec=t.data["spec_checked"],
                                    out_path=os.path.join(t.data["folder"], "review.png"),
                                    original_size=t.data["original_size"],
                                    snap=bool(self.cfg.get("review_snap_targets", True)),
                                    layout=bool(self.cfg.get("review_auto_layout", True)))
            t.data["spec"], t.data["review_image"] = out["spec"], out["image_path"]
            t.artifacts.append(out["image_path"])

        def save(t):
            spec, rid, note, sid = t.data["spec"], t.data["case_id"], t.data["note"], t.data["sid"]
            original = Image.open(t.data["input_image"])
            meta = cr.store_review(original, Image.open(t.data["review_image"]), spec, note, source=t.data["source"],
                                   rid=rid, reviews_dir=self.reviews_dir,
                                   extra={"mode": "review", "task_id": t.task_id, "validation": t.validation})
            marks = [{k: a.get(k) for k in ("annotation_id", "label", "type", "status", "text")}
                     for a in spec["annotations"] if a["kind"] == "callout"]
            case = {"case_id": rid, "analysis_timestamp": t.data["analysis_timestamp"], "mode": "review",
                    "symbol": _symbol(note), "timeframe": _timeframe(note), "note": note,
                    "original_path": t.data["input_image"], "annotated_path": t.data["review_image"],
                    "image_w": original.width, "image_h": original.height,
                    "market_context": spec.get("market_context", ""),
                    "setup_classification": ", ".join(sorted({m["type"] for m in marks})),
                    "initial_hypothesis": {"always_in": spec.get("always_in"), "current_state": spec.get("current_state"),
                                           "summary_lines": spec.get("summary_lines"), "analysis": spec.get("analysis"),
                                           "marks": marks},
                    "supporting_evidence": {"knowledge_version": getattr(self.kb, "version", None),
                                            "primer": self.cfg.get("knowledge_primer"), "similar_cases":
                                            [c["case_id"] for c in t.data.get("similar_cases", [])]},
                    "data_quality": {"source": "screenshot", "limitations": spec.get("limitations", "")},
                    "task_id": t.task_id}
            if self.memory.case(rid) is None:
                self.harness.tool(t, "save_review_case", case=case, annotations=spec["annotations"])
                t.memory_writes.append(f"case:{rid}")
            text = review_text(spec, t.validation)
            self.memory.add_message(sid, "user", f"[chart] {note}", "review")
            self.memory.add_message(sid, "assistant", text, "review", [rid])
            self.memory.add_discussion(rid, "coach", text, t.task_id)
            self.memory.update_session(sid, last_mode="review", current_case_id=rid, current_annotation_id=None,
                                       current_symbol=_symbol(note))
            t.data["meta"], t.data["text"] = meta, text

        return [("retrieve", "RETRIEVING", retrieve), ("analyze", "ANALYZING", analyze),
                ("verify", "VERIFYING", verify), ("render", "RENDERING", render), ("save", "SAVING", save)]

    # ------------------------------------------------------------ DISCUSS / EXPLAIN
    def explain(self, request: dict, sid: str) -> dict:
        """A question about one mark: the same ongoing case discussion, focused on that mark."""
        return self.discuss(request, sid, require_focus=True)

    def discuss(self, request: dict, sid: str, require_focus: bool = False) -> dict:
        """Follow-up questions and challenges about the case in focus, with the reasoning chain,
        alternatives, what would change the coach's mind, revisions (versioned) and a question back."""
        question = (request.get("text") or "").strip() or "请解释这个判断的理由。"
        state = self.memory.session(sid)["state"]
        case_id = request.get("case_id") or state.get("current_case_id")
        ref = request.get("annotation_id") or find_annotation_ref(question)
        task = self._task("explain" if require_focus or ref else "discuss", request, sid)

        def validate(t):
            if not case_id:
                raise ValueError("No chart in focus: give case_id or review a chart first")
            case = self.memory.case(case_id)
            if case is None:
                raise ValueError(f"Case {case_id} not found")
            anns = self.memory.annotations(case_id)
            focus = resolve_annotation(anns, ref) if (ref or require_focus) else None
            if (ref or require_focus) and focus is None:
                listing = ", ".join(f"{a['label']}={a['annotation_id']}" for a in anns if a.get("kind") == "callout")
                raise ValueError(f"Mark {ref!r} not found in case {case_id}. Available: {listing or 'none'}")
            pending = state.get("pending_question") if state.get("pending_case_id") == case_id else ""
            t.data.update(case=case, annotations=anns, focus=focus, challenge=exp_is_challenge(question),
                          view=self.memory.current_view(case_id), history=self.memory.discussion(case_id, 24),
                          pending=pending or "")

        def retrieve(t):
            focus, case = t.data["focus"], t.data["case"]
            q = question + " " + (f"{focus.get('type', '')} {focus.get('text', '')}" if focus
                                  else str(case.get("setup_classification") or ""))
            t.data["evidence"] = self._retrieve(t, q, k_canonical=5, k_derived=3)
            t.data["learning"] = self.harness.tool(t, "read_learning_memory")

        def analyze(t):
            case, focus = t.data["case"], t.data["focus"]
            images = [p for p in (case.get("annotated_path"), case.get("original_path")) if p and os.path.isfile(p)][:1]
            if focus:
                coords = focus["coordinates"]
                x, y = (coords["x"], coords["y"]) if "x" in coords else \
                    ((coords["x1"] + coords["x2"]) / 2, (coords["y1"] + coords["y2"]) / 2)
                crop_path = os.path.join(self.data_dir, "crops", f"{case_id}_{focus['annotation_id']}_v{focus['version']}.png")
                os.makedirs(os.path.dirname(crop_path), exist_ok=True)
                cr.crop_around(Image.open(case["original_path"]), x, y).save(crop_path)
                images.append(crop_path)
            system = disc.build_system(case, t.data["view"], t.data["annotations"], focus, t.data["history"],
                                       t.data["pending"], t.data["learning"], t.data["evidence"])
            q = question + ("\n(The user is challenging the analysis: re-examine it honestly.)" if t.data["challenge"] else "")
            t.data["raw"] = self.harness.tool(t, "explain_annotation", system=system, question=q, image_paths=images)

        def verify(t):
            from knowledge.citations import verify_claims
            result = t.data["raw"]
            ids = {e["evidence_id"]: e for e in t.data["evidence"]}
            claims, issues = verify_claims(result.get("claims", []), ids)
            result["claims"] = claims
            t.data["step_issues"] = disc.check_steps(result, set(ids))
            t.validation += issues + [{"flag": i} for i in t.data["step_issues"]]
            t.data["result"] = result

        def save(t):
            r, focus = t.data["result"], t.data["focus"]
            applied, revision = self._apply_revisions(case_id, r["revisions"], question, t)
            if t.data["challenge"]:
                self.memory.add_feedback(case_id, question, focus["annotation_id"] if focus else None)
                t.memory_writes.append(f"feedback:{case_id}")
            for obs in r.get("candidate_observations", [])[:2]:
                out = self.harness.tool(t, "record_candidate_insight", kind=obs["kind"], content=obs["content"],
                                        rationale=obs["rationale"], source_type="agent", source_ref=f"task:{t.task_id}")
                t.memory_writes.append(f"candidate:{out['candidate_id']}")
            if focus:   # show the mark as it is now (after a revision in this turn)
                focus = self.memory.annotation(case_id, focus["annotation_id"]) or focus
            text = disc.format_discussion(r, t.data["evidence"], focus, applied, t.data["step_issues"])
            refs = [focus["annotation_id"]] if focus else []
            self.memory.add_discussion(case_id, "user", question, t.task_id, refs)
            self.memory.add_discussion(case_id, "coach", text, t.task_id, [e["doc_id"] for e in t.data["evidence"]])
            self.memory.add_message(sid, "user", question, "discuss", [case_id] + refs)
            self.memory.add_message(sid, "assistant", text, "discuss", [e["doc_id"] for e in t.data["evidence"]])
            self.memory.update_session(sid, last_mode="discuss", current_case_id=case_id,
                                       current_annotation_id=focus["annotation_id"] if focus else state.get("current_annotation_id"),
                                       pending_question=r.get("follow_up_question", ""), pending_case_id=case_id)
            t.memory_writes += ["discussion", "messages"]
            t.data.update(text=text, revision=revision, applied=applied)

        self.harness.run(task, [("validate", "VALIDATING", validate), ("retrieve", "RETRIEVING", retrieve),
                                ("analyze", "ANALYZING", analyze), ("verify", "VERIFYING", verify),
                                ("save", "SAVING", save)])
        r = task.data["result"]
        return {"task_id": task.task_id, "case_id": case_id, "text": task.data["text"],
                "annotation_id": task.data["focus"]["annotation_id"] if task.data["focus"] else None,
                "revision": task.data["revision"], "revisions_applied": task.data["applied"],
                "user_point": r["user_point"], "follow_up_question": r.get("follow_up_question", ""),
                "evidence": _strip(task.data["evidence"]), "validation": task.validation}

    def _apply_revisions(self, case_id: str, revisions: list[dict], question: str, t: Task) -> tuple[list[str], dict | None]:
        """Stores the coach's changes of mind as new versions; returns readable notes + last image."""
        applied, revision = [], None
        for rv in revisions[:6]:
            reason = f"{rv['reason']} (user: {question[:200]})"
            if rv["target"] == "annotation":
                ann = self.memory.annotation(case_id, rv["annotation_id"])
                if not ann or ann.get("kind") != "callout":
                    t.validation.append({"flag": f"revision for unknown mark {rv['annotation_id']!r} ignored"})
                    continue
                revision = self._revise(case_id, ann, rv, reason)
                what = "撤回" if rv["new_status"] == "withdrawn" else f"改为 {rv['new_type']}（{rv['new_status']}）"
                applied.append(f"{ann['label']} {ann['annotation_id']}：{what}，版本 v{revision['version']} — {rv['reason']}")
                t.memory_writes.append(f"annotation:{case_id}/{ann['annotation_id']}@v{revision['version']}")
                t.artifacts.append(revision["image"])
            else:
                value = rv["new_value"].strip()
                if rv["target"] == "decision_bias" and value.upper() not in ("LONG", "SHORT", "WAIT"):
                    t.validation.append({"flag": f"invalid bias revision {value!r} ignored"})
                    continue
                if rv["target"] == "always_in" and value.lower() not in ("long", "short", "unclear"):
                    t.validation.append({"flag": f"invalid always-in revision {value!r} ignored"})
                    continue
                if not value:
                    continue
                value = value.upper() if rv["target"] == "decision_bias" else value
                self.memory.add_case_revision(case_id, rv["target"], value, reason, t.task_id)
                applied.append(f"{rv['target']} → {value} — {rv['reason']}")
                t.memory_writes.append(f"revision:{case_id}/{rv['target']}")
        return applied, revision

    def _revise(self, case_id: str, ann: dict, rv: dict, reason: str) -> dict:
        new = dict(ann)
        if rv["new_status"] == "withdrawn":
            new["status"] = "withdrawn"
        else:
            new.update(status=rv["new_status"], text=rv["new_value"] or ann.get("text", ""))
            if rv["new_type"] != "none":
                new["type"] = rv["new_type"]
            if 0 < rv["new_target_x"] <= cr.GRID and 0 < rv["new_target_y"] <= cr.GRID:
                new["coordinates"] = {**ann["coordinates"], "x": rv["new_target_x"], "y": rv["new_target_y"]}
            new["reason"] = rv["reason"]
        for k in ("version", "change_reason", "created", "superseded"):
            new.pop(k, None)
        version = self.memory.revise_annotation(case_id, ann["annotation_id"], new, reason)
        # redraw from the saved spec + every current annotation version; the old image stays
        meta_path = os.path.join(self.reviews_dir, case_id, "meta.json")
        with open(meta_path, encoding="utf-8") as f:
            spec = json.load(f)["spec"]
        current = {a["annotation_id"]: a for a in self.memory.annotations(case_id) if a.get("kind") == "callout"}
        callouts = []
        for c in spec.get("callouts", []):
            a = current.get(c.get("annotation_id"))
            if a and a.get("status") == "withdrawn":
                continue
            if a:
                c = {**c, "type": a["type"], "text": a["text"], "status": a["status"], "reason": a.get("reason", ""),
                     "target_x": a["coordinates"]["x"], "target_y": a["coordinates"]["y"]}
            callouts.append(c)
        spec["callouts"] = callouts
        case = self.memory.case(case_id)
        out = os.path.join(self.reviews_dir, case_id, f"review_v{version}.png")
        cr.render(Image.open(case["original_path"]).convert("RGB"), spec).save(out)
        self.memory.set_annotated_path(case_id, out)
        return {"version": version, "image": out}

    # ------------------------------------------------------------ DECISION
    def decide(self, request: dict, sid: str) -> dict:
        image = self._image_from(request)
        question = (request.get("text") or DEFAULT_TRADE_QUESTION).strip()
        settings = dec.trading_settings(self.cfg)
        task = self._task("decision", request, sid)
        rid = cr.new_review_id()
        folder = os.path.join(self.reviews_dir, rid)

        def validate(t):
            if image is None and not request.get("csv"):
                raise ValueError("Decision mode needs a chart screenshot or OHLC data (csv)")
            t.data["analysis_timestamp"] = datetime.now().isoformat(timespec="seconds")
            if image is not None:
                os.makedirs(folder, exist_ok=True)
                cr.prepare(image).save(os.path.join(folder, "original.png"))
                t.data["input_image"] = os.path.join(folder, "original.png")

        def retrieve(t):
            if request.get("csv"):
                md = self.harness.tool(t, "get_market_data", csv=request["csv"], as_of=request.get("as_of"))
                t.data["market"] = md
            t.data["learning"] = self.harness.tool(t, "read_learning_memory")
            t.data["evidence"] = self._retrieve(t, question, extra_terms=["signal bar", "entry", "stop", "always in",
                                                                          "trader's equation"])

        def analyze(t):
            md = t.data.get("market")
            note = (f"OHLC data up to {md['summary'].get('last_time')}, {md['dropped_future_bars']} later bars "
                    "were removed before analysis") if md else "screenshot only; the last bar on the screenshot"
            system = dec.build_system(t.data["evidence"], t.data["learning"], md["summary"] if md else None,
                                      bool(self.cfg.get("decisive_style", True)), note, settings)
            t.data["data_note"] = note
            t.data["raw"] = self.harness.tool(t, "evaluate_trade_hypothesis", system=system, question=question,
                                              image_path=t.data.get("input_image"))

        def verify(t):
            from market.ohlc import Bar
            bars = [Bar(datetime.fromisoformat(b[0]), *b[1:]) for b in t.data["market"]["bars"]] if t.data.get("market") else None
            hint = " ".join(str(request.get(k) or "") for k in ("symbol", "note")) + " " + question
            result, issues = dec.validate_decision(t.data["raw"], t.data["evidence"], bars, settings, symbol_hint=hint)
            t.data["result"], t.validation = result, t.validation + [{"decision": i} for i in issues]
            t.data["issues"] = issues

        def save(t):
            r = t.data["result"]
            md = t.data.get("market")
            dq = {"source": "OHLC + screenshot" if md and image is not None else "OHLC" if md else "screenshot",
                  "time_range": f"{md['summary']['first_time']} → {md['summary']['last_time']}" if md else r.get("data_time_range"),
                  "dropped_future_bars": md["dropped_future_bars"] if md else 0, "note": t.data["data_note"]}
            plan_path = None
            if image is not None:
                orig = Image.open(t.data["input_image"])
                plan_img, plan_issues = plan_chart.render_plan(orig, r, dec.headline(r, plain=True))
                t.data["issues"] += plan_issues
                t.validation += [{"plan_chart": i} for i in plan_issues]
            text = dec.format_decision(r, t.data["evidence"], t.data["issues"], dq)
            if image is not None:
                cr.store_review(orig, plan_img, {"analysis": text, "summary": f"{dec.headline(r)}\n{r['conclusion']}"},
                                question, source=request.get("source", "web"), rid=rid, reviews_dir=self.reviews_dir,
                                extra={"mode": "decision", "task_id": t.task_id, "decision": r})
                plan_path = os.path.join(folder, "review.png")
            t.data["plan_image"] = plan_path
            case = {"case_id": rid, "analysis_timestamp": t.data["analysis_timestamp"], "mode": "decision",
                    "symbol": _symbol(question) or r["position"].get("symbol") or r["chart_read"].get("symbol") or None,
                    "timeframe": _timeframe(question) or r["chart_read"].get("timeframe") or None, "note": question,
                    "original_path": t.data.get("input_image"), "annotated_path": plan_path,
                    "market_context": r["market_context"],
                    "setup_classification": r["primary_setup"]["type"], "initial_hypothesis": r,
                    "supporting_evidence": {"evidence": [e["doc_id"] for e in t.data["evidence"]],
                                            "knowledge_version": getattr(self.kb, "version", None)},
                    "data_quality": dq, "task_id": t.task_id,
                    "market_timestamp": md["summary"].get("last_time") if md else None}
            self.harness.tool(t, "save_review_case", case=case, annotations=[])
            t.memory_writes.append(f"case:{rid}")
            self.memory.add_message(sid, "user", question, "decision")
            self.memory.add_message(sid, "assistant", text, "decision", [rid])
            self.memory.update_session(sid, last_mode="decision", current_case_id=rid, last_bias=r["bias"],
                                       pending_question=r.get("follow_up_question", ""), pending_case_id=rid)
            self.memory.add_discussion(rid, "user", question, t.task_id)
            self.memory.add_discussion(rid, "coach", text, t.task_id)
            t.data["text"] = text

        self.harness.run(task, [("validate", "VALIDATING", validate), ("retrieve", "RETRIEVING", retrieve),
                                ("analyze", "ANALYZING", analyze), ("verify", "VERIFYING", verify),
                                ("save", "SAVING", save)])
        return {"task_id": task.task_id, "case_id": rid, "text": task.data["text"], "decision": task.data["result"],
                "image_path": task.data.get("plan_image"),
                "validation": task.validation, "evidence": _strip(task.data["evidence"])}

    # ------------------------------------------------------------ OUTCOME / REFLECTION
    def record_outcome(self, request: dict, sid: str) -> dict:
        case_id = request.get("case_id") or self.memory.session(sid)["state"].get("current_case_id")
        outcome = (request.get("text") or "").strip()
        task = self._task("outcome", request, sid)

        def validate(t):
            if not case_id or self.memory.case(case_id) is None:
                raise ValueError("Unknown case")
            if not outcome:
                raise ValueError("Describe what happened after the analysis")
            t.data["case"] = self.memory.case(case_id)

        def analyze(t):
            system = refl.build_system(t.data["case"], outcome, request.get("source", "user"))
            t.data["raw"] = self.llm.json_call(system=system, content="Evaluate the analysis against the outcome.",
                                               schema_name="reflection", schema=refl.REFLECT_SCHEMA, purpose="study",
                                               max_tokens=3000, recorder=t.record_llm)

        def save(t):
            r = t.data["raw"]
            self.memory.add_outcome(case_id, outcome, r["direction_moved"], r, source_type="user")
            ids = []
            for lesson in r["lessons"][:4]:
                ids.append(self.harness.tool(t, "record_candidate_insight", kind=lesson["kind"], content=lesson["content"],
                                             rationale=lesson["rationale"], source_type="reflection",
                                             source_ref=f"case:{case_id}")["candidate_id"])
            t.memory_writes += [f"outcome:{case_id}"] + [f"candidate:{i}" for i in ids]
            t.data["text"] = refl.format_reflection(r, ids)

        self.harness.run(task, [("validate", "VALIDATING", validate), ("analyze", "ANALYZING", analyze),
                                ("save", "SAVING", save)])
        return {"task_id": task.task_id, "case_id": case_id, "text": task.data["text"], "reflection": task.data["raw"]}

    def resume(self, task_id: str) -> Task:
        """Continues a RECOVERABLE/FAILED review from its last checkpoint: finished steps (and
        their model calls) are not repeated."""
        task = self.tasks.load(task_id)
        if task is None or task.state not in ("RECOVERABLE", "FAILED"):
            raise ValueError("Only RECOVERABLE or FAILED tasks can be resumed")
        if task.mode != "review" or "validate" not in task.completed_steps:
            raise ValueError("Only reviews that got past validation can be resumed")
        CURRENT_TASK.set(task)
        return self.harness.run(task, self._review_steps())


# ---------------------------------------------------------------- helpers

BAR_LEVEL_TYPES = {"H1", "H2", "H3", "H4", "L1", "L2", "L3", "L4", "signal_bar", "entry_bar", "reversal_bar",
                   "sell_climax", "buy_climax", "micro_channel", "ema_test", "gap", "follow_through"}
UNREADABLE_RE = re.compile(r"看不清|不能看清|无法看清|无法辨认|难以辨认|模糊|不清晰|cannot (read|see)|can't (read|see)|"
                           r"unclear|illegible|not legible|too small", re.I)


def validate_review_spec(spec: dict) -> tuple[dict, list[dict]]:
    """Checks the model's review before drawing: ids are assigned later, here we catch
    out-of-range coordinates and over-confident marks."""
    issues = []
    bars_unreadable = bool(UNREADABLE_RE.search(spec.get("limitations", "")))
    for i, c in enumerate(spec.get("callouts", [])):
        for k in ("target_x", "target_y", "label_x", "label_y"):
            if not 0 <= float(c.get(k, -1)) <= cr.GRID:
                issues.append({"callout": i, "flag": f"{k} out of range, clamped"})
        if c.get("status") == "confirmed" and len(c.get("reason", "").strip()) < 8:
            c["status"] = "candidate"
            issues.append({"callout": i, "flag": "confirmed without a reason -> candidate"})
        if c.get("type") not in cr.ANNOTATION_TYPES:
            c["type"] = "other"
        if bars_unreadable and c.get("status") == "confirmed" and c.get("type") in BAR_LEVEL_TYPES:
            c["status"] = "candidate"
            issues.append({"callout": i, "flag": f"{c['type']} cannot be confirmed when the bars are not readable -> candidate"})
    if not spec.get("current_state", "").strip():
        issues.append({"flag": "no reading of the right edge (current_state)"})
    if not spec.get("callouts"):
        issues.append({"flag": "no callouts"})
    return spec, issues


def resolve_annotation(annotations: list[dict], ref: str | None) -> dict | None:
    if not ref:
        callouts = [a for a in annotations if a.get("kind") == "callout"]
        return callouts[0] if len(callouts) == 1 else None
    for a in annotations:
        if ref in (a.get("label"), a.get("annotation_id")):
            return a
    return None


def review_text(spec: dict, issues: list[dict]) -> str:
    marks = [a for a in spec.get("annotations", []) if a.get("kind") == "callout"]
    lines = [f"**市场背景**：{spec.get('market_context', '')}（Always In：{spec.get('always_in', '')}）",
             f"**右侧现状**：{spec.get('current_state', '')}", "", "**复盘分析**", spec.get("analysis", ""), "",
             "**图上标注与理由**（可以继续追问或质疑，例如「为什么 #3 是 H2 而不是 H1？」「我觉得 #2 标错了」）"]
    for a in marks:
        lines.append(f"- {a['label']} `{a['annotation_id']}` {a['text'].replace(chr(10), ' ')} · {a['status']}"
                     + (" · 已吸附到K线" if a.get("snapped") else ""))
        if a.get("reason"):
            lines.append(f"  理由：{a['reason']}" + (f"（K线：{a['bar_ref']}）" if a.get("bar_ref") else ""))
    if spec.get("limitations"):
        lines += ["", f"**限制**：{spec['limitations']}"]
    if issues:
        lines += ["", "🔎 校验：" + "；".join(i.get("flag", str(i)) for i in issues)]
    return "\n".join(lines)


def _symbol(text: str) -> str | None:
    m = re.search(r"\b(MYM|MES|MNQ|MGC|ES|NQ|YM|GC|CL|SPY|QQQ|[A-Z]{2,5}[FGHJKMNQUVXZ]20\d\d)\b", (text or "").upper())
    return m.group(1) if m else None


def _timeframe(text: str) -> str | None:
    m = re.search(r"(\d+)\s*(分钟|min|m\b|分)", text or "", re.I)
    return f"{m.group(1)}m" if m else None
