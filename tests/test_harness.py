"""Harness: lifecycle, failures, tool timeouts/retries/validation, redaction, resume from checkpoint."""

import os
import shutil
import tempfile
import time
import unittest

from pydantic import BaseModel

from agent.harness import Harness, TaskStore, Tool, ToolError, ToolRegistry, Tracer, redact
from agent.router import find_annotation_ref, route
from tests.helpers import FixtureCase


class In(BaseModel):
    x: int


class Out(BaseModel):
    y: int


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="harness_test_")
        self.store = TaskStore(os.path.join(self.tmp, "agent.db"))
        self.reg = ToolRegistry()
        self.h = Harness(self.store, self.reg, Tracer(os.path.join(self.tmp, "traces")))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_states_and_failure(self):
        task = self.h.new_task("study", {"text": "q"})
        self.h.run(task, [("a", "VALIDATING", lambda t: None), ("b", "ANALYZING", lambda t: t.data.update(v=1))])
        self.assertEqual([e[1] for e in self.store.events(task.task_id)][-1], "COMPLETED")
        bad = self.h.new_task("study", {})
        with self.assertRaises(ValueError):
            self.h.run(bad, [("a", "VALIDATING", lambda t: (_ for _ in ()).throw(ValueError("boom")))])
        loaded = self.store.load(bad.task_id)
        self.assertEqual(loaded.state, "FAILED")
        self.assertIn("boom", loaded.errors[0]["error"])

        class APIConnectionError(Exception):
            pass
        flaky = self.h.new_task("review", {})
        with self.assertRaises(APIConnectionError):
            self.h.run(flaky, [("a", "ANALYZING", lambda t: (_ for _ in ()).throw(APIConnectionError("net")))])
        self.assertEqual(self.store.load(flaky.task_id).state, "RECOVERABLE")

    def test_tool_validation_timeout_retry(self):
        calls = {"n": 0}

        def flaky(x):
            calls["n"] += 1
            if calls["n"] < 2:
                raise ConnectionError("try again")
            return {"y": x * 2}
        self.reg.register(Tool("flaky", "d", flaky, In, Out, retries=2, timeout_s=5))
        self.reg.register(Tool("slow", "d", lambda x: time.sleep(2) or {"y": 1}, In, Out, timeout_s=0.2))
        self.reg.register(Tool("badout", "d", lambda x: {"z": 1}, In, Out))
        task = self.h.new_task("t", {})
        self.assertEqual(self.h.tool(task, "flaky", x=2), {"y": 4})
        self.assertEqual(task.tool_calls[-1]["attempts"], 2)
        with self.assertRaises(ToolError):
            self.h.tool(task, "slow", x=1)
        with self.assertRaises(ToolError):
            self.h.tool(task, "badout", x=1)
        with self.assertRaises(ToolError):
            self.h.tool(task, "flaky", x="not a number")
        with self.assertRaises(ToolError):
            self.h.tool(task, "missing", x=1)
        self.assertFalse(task.tool_calls[-1]["ok"])

    def test_redaction(self):
        r = redact({"api_key": "sk-123", "image": "data:image/png;base64," + "A" * 5000, "Authorization": "Bearer x",
                    "nested": {"password": "p"}, "text": "ok"})
        self.assertEqual(r["api_key"], "***")
        self.assertEqual(r["Authorization"], "***")
        self.assertEqual(r["nested"]["password"], "***")
        self.assertLess(len(r["image"]), 120)
        self.assertEqual(r["text"], "ok")


class RouterTests(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(route({"text": "解释 H2"}), "study")
        self.assertEqual(route({"text": "MYM 5分钟，我在高点做空，止损被打", "image": object()}), "review")
        self.assertEqual(route({"text": "现在我应该更关注做多还是做空？", "image": object()}), "decision")
        self.assertEqual(route({"text": "为什么 #3 是 H2？"}, {"current_case_id": "c1"}), "explain")
        self.assertEqual(route({"text": "为什么 #3 是 H2？"}, {}), "study")
        self.assertEqual(route({"text": "以后请记住，所有 H2 都是高概率买点"}), "memory")
        self.assertEqual(route({"text": "x", "mode": "outcome"}), "outcome")
        self.assertEqual(find_annotation_ref("H2_001 为什么"), "H2_001")
        self.assertEqual(find_annotation_ref("#12 呢"), "#12")
        self.assertEqual(find_annotation_ref("SELL_CLIMAX_002?"), "SELL_CLIMAX_002")


class ResumeTests(FixtureCase):
    def test_failed_render_resumes_without_new_model_calls(self):
        agent = self.make_agent()
        original = agent.tools.tools["annotate_chart"].fn
        agent.tools.tools["annotate_chart"].fn = lambda **kw: (_ for _ in ()).throw(OSError("disk full"))
        out = agent.handle({"image_path": self.chart, "text": "MYM"})
        self.assertFalse(out["ok"])
        task = agent.tasks.load(out["task_id"])
        self.assertEqual(task.state, "FAILED")
        self.assertIn("spec_checked", task.data)
        calls_before = len(self.llm.calls)
        agent.tools.tools["annotate_chart"].fn = original
        done = agent.resume(out["task_id"])
        self.assertEqual(done.state, "COMPLETED")
        self.assertEqual(len(self.llm.calls), calls_before)
        self.assertTrue(os.path.isfile(done.data["review_image"]))
