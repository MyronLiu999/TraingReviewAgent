"""Chart review: legacy rendering, annotation registry, coordinate mapping, snapping, explain lookup."""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

import chart_review as cr
from agent.runtime import resolve_annotation, validate_review_spec
from tests.helpers import PROJECT, FixtureCase, chart_spec, make_chart


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chart_test_")
        self.path = make_chart(os.path.join(self.tmp, "c.png"))
        self.chart = cr.prepare(Image.open(self.path))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_spec_still_renders(self):
        old = {"chart_description": "", "zones": [{"kind": "trading_range", "x1": 100, "y1": 100, "x2": 300, "y2": 300,
                                                   "title": "TR"}],
               "rings": [{"x": 200, "y": 200, "size": "small", "color": "blue"}],
               "callouts": [{"text": "Double Top 双顶", "color": "magenta", "label_x": 500, "label_y": 100,
                             "target_x": 200, "target_y": 200, "arrow": True}],
               "summary": "① a\n② b", "summary_x": 700, "summary_y": 600, "analysis": "x"}
        img = cr.render(self.chart, old)
        self.assertEqual(img.size, self.chart.size)
        reg = cr.build_registry(old, self.chart.size)
        self.assertEqual(reg[0]["annotation_id"], "OTHER_001")

    def test_registry_ids_labels_and_ring_links(self):
        spec = chart_spec()
        spec["callouts"].append({**spec["callouts"][0], "target_x": 700})
        cr.finalize_spec(self.chart, spec, snap=False)
        ids = [(a["label"], a["annotation_id"]) for a in spec["annotations"] if a["kind"] == "callout"]
        self.assertEqual(ids, [("#1", "H2_001"), ("#2", "H1_001"), ("#3", "BREAKOUT_001"), ("#4", "H2_002")])
        ring = next(a for a in spec["annotations"] if a["kind"] == "ring")
        self.assertEqual(ring["linked_to"], "H2_001")
        self.assertEqual(next(a for a in spec["annotations"] if a["kind"] == "zone")["annotation_id"], "Z1")

    def test_coordinates_map_to_any_image_size(self):
        spec = chart_spec()
        cr.finalize_spec(self.chart, spec, snap=False, original_size=(2400, 1400))
        a = spec["annotations"][0]
        self.assertEqual(a["image_size"], list(self.chart.size))
        self.assertEqual(a["original_size"], [2400, 1400])
        x, y = a["coordinates"]["x"], a["coordinates"]["y"]
        self.assertEqual(cr.grid_to_px(x, y, self.chart.size), (a["pixel"]["x"], a["pixel"]["y"]))
        big = cr.grid_to_px(x, y, (2400, 1400))
        self.assertAlmostEqual(big[0] / 2400, a["pixel"]["x"] / self.chart.size[0], places=4)
        gx, gy = cr.px_to_grid(*big, (2400, 1400))
        self.assertAlmostEqual((gx, gy)[0], x, places=6)

    def test_snapping_moves_arrow_onto_candle_extreme_and_records_it(self):
        # find the lowest-price bar (largest y) in the synthetic chart around x of bar 21
        spec = chart_spec()
        bar_x = (60 + 21 * 26) * cr.GRID / self.chart.width
        spec["callouts"][0].update(target_x=bar_x, target_y=300)
        cr.finalize_spec(self.chart, spec, snap=True)
        c = spec["callouts"][0]
        self.assertIsNotNone(c.get("snapped"))
        self.assertEqual(c["snapped"]["from_y"], 300)
        expected = cr.candle_extent(self.chart, bar_x, 300, "bottom")
        self.assertAlmostEqual(c["target_y"], expected + 6, delta=1)
        self.assertEqual(spec["annotations"][0]["snapped"], c["snapped"])

    def test_unconfirmed_structures_are_not_promoted(self):
        spec = chart_spec()
        spec["callouts"][2]["reason"] = ""
        spec, issues = validate_review_spec(spec)
        self.assertEqual(spec["callouts"][2]["status"], "candidate")       # confirmed without reason -> candidate
        self.assertEqual(spec["callouts"][1]["status"], "uncertain")       # uncertain stays uncertain
        self.assertTrue(any("confirmed without a reason" in i.get("flag", "") for i in issues))

    def test_resolve_annotation_by_label_or_id(self):
        spec = chart_spec()
        cr.finalize_spec(self.chart, spec, snap=False)
        anns = spec["annotations"]
        self.assertEqual(resolve_annotation(anns, "#2")["annotation_id"], "H1_001")
        self.assertEqual(resolve_annotation(anns, "BREAKOUT_001")["label"], "#3")
        self.assertIsNone(resolve_annotation(anns, "#9"))

    def test_summary_is_wrapped_inside_the_image(self):
        spec = chart_spec()
        spec["summary_lines"] = ["当前：" + "很长的总结" * 40]
        img = cr.render(self.chart, spec)
        self.assertEqual(img.size, self.chart.size)

    def test_legacy_cli_with_saved_spec(self):
        spec_path = os.path.join(self.tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(chart_spec(), f)
        out = subprocess.run([sys.executable, os.path.join(PROJECT, "chart_review.py"), self.path, "--spec", spec_path],
                             capture_output=True, text=True, encoding="utf-8", timeout=120,
                             env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(out.returncode, 0, out.stderr)
        m = re.search(r"Saved: (reviews[\\/](\S+?)[\\/]review\.png)", out.stdout)
        self.assertTrue(m, out.stdout)
        folder = os.path.join(PROJECT, "reviews", m.group(2))
        try:
            self.assertTrue(os.path.isfile(os.path.join(PROJECT, m.group(1))))
            meta = json.load(open(os.path.join(folder, "meta.json"), encoding="utf-8"))
            self.assertEqual(meta["annotations"][0]["annotation_id"], "H2_001")
        finally:
            shutil.rmtree(folder, ignore_errors=True)        # remove only the folder this test created


class ReviewFlowTests(FixtureCase):
    def test_review_records_task_lifecycle_and_case(self):
        agent = self.make_agent()
        out = agent.handle({"image_path": self.chart, "text": "MGC 5分钟"})
        self.assertTrue(out["ok"], out.get("error"))
        states = [e[1] for e in agent.tasks.events(out["task_id"])]
        for s in ("CREATED", "VALIDATING", "RETRIEVING", "ANALYZING", "VERIFYING", "RENDERING", "SAVING", "COMPLETED"):
            self.assertIn(s, states)
        task = agent.tasks.load(out["task_id"])
        self.assertTrue(any(c["tool"] == "analyze_chart_structure" for c in task.tool_calls))
        self.assertTrue(task.llm_calls)
        self.assertIn(f"case:{out['case_id']}", task.memory_writes)
        meta = json.load(open(os.path.join(self.tmp, "reviews", out["case_id"], "meta.json"), encoding="utf-8"))
        self.assertEqual(meta["mode"], "review")
        self.assertEqual(agent.memory.case(out["case_id"])["symbol"], "MGC")
        self.assertIn("fixture", self.llm.calls[0]["system"])      # primer came from the canonical snapshot


class QualityGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chart_q_")
        self.chart = cr.prepare(Image.open(make_chart(os.path.join(self.tmp, "c.png"))))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bar_level_marks_capped_when_bars_unreadable(self):
        spec = chart_spec()
        spec["callouts"][0]["status"] = "confirmed"            # H2
        spec["limitations"] = "不能看清每根K线的细节"
        spec, issues = validate_review_spec(spec)
        self.assertEqual(spec["callouts"][0]["status"], "candidate")
        self.assertEqual(spec["callouts"][2]["status"], "confirmed")   # breakout is a broad structure: kept

    def test_arrow_on_empty_space_moves_to_nearest_candles(self):
        spec = chart_spec()
        bar_x = (60 + 5 * 26) * cr.GRID / self.chart.width
        spec["callouts"][2].update(type="breakout", target_x=bar_x, target_y=700)   # below the bars, within snap range
        spec["rings"] = [{"x": bar_x, "y": 100, "size": "small", "color": "green"}]   # far above, not on an arrow
        cr.finalize_spec(self.chart, spec, snap=True)
        self.assertLess(spec["callouts"][2]["target_y"], 700)
        self.assertEqual(spec["callouts"][2]["snapped"]["edge"], "bottom")
        self.assertGreater(spec["rings"][0]["y"], 100)
        self.assertIn("snapped", spec["rings"][0])


class LabelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chart_layout_")
        self.chart = cr.prepare(Image.open(make_chart(os.path.join(self.tmp, "c.png"))))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def far_spec(self):
        spec = chart_spec()
        for i, c in enumerate(spec["callouts"]):      # the model stacked every label in a far column
            c.update(label_x=880, label_y=60 + i * 60)
        return spec

    def boxes(self, spec):
        from PIL import ImageDraw, ImageFont
        w, h = self.chart.size
        unit, pad = w / 2000, 6 * w / 2000
        fp = cr.find_font()
        f = ImageFont.truetype(fp, round(18 * unit)) if fp else ImageFont.load_default()
        d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        out = []
        for c in spec["callouts"]:
            b = d.multiline_textbbox((0, 0), cr.callout_text(c), font=f, spacing=4)
            x, y = c["label_x"] * w / cr.GRID, c["label_y"] * h / cr.GRID
            out.append((x - pad, y - pad, x + b[2] - b[0] + pad, y + b[3] - b[1] + pad))
        return out

    def test_labels_move_next_to_their_targets(self):
        spec = self.far_spec()
        before = [math.dist((c["label_x"], c["label_y"]), (c["target_x"], c["target_y"])) for c in spec["callouts"]]
        cr.finalize_spec(self.chart, spec, snap=False)
        after = [math.dist((c["label_x"], c["label_y"]), (c["target_x"], c["target_y"])) for c in spec["callouts"]]
        self.assertLess(sum(after), sum(before) * 0.6)
        self.assertTrue(all(c.get("label_from") for c in spec["callouts"]))
        reg = {a["annotation_id"]: a for a in spec["annotations"]}
        for c in spec["callouts"]:
            self.assertEqual(reg[c["annotation_id"]]["coordinates"]["label_x"], c["label_x"])

    def test_labels_do_not_overlap_or_cover_targets(self):
        spec = self.far_spec()
        cr.finalize_spec(self.chart, spec, snap=False)
        boxes = self.boxes(spec)
        w, h = self.chart.size
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertEqual(cr._overlap(a, b), 0)
            for c in spec["callouts"]:
                tx, ty = c["target_x"] * w / cr.GRID, c["target_y"] * h / cr.GRID
                self.assertFalse(a[0] < tx < a[2] and a[1] < ty < a[3])
        self.assertEqual(cr.render(self.chart, spec).size, self.chart.size)

    def test_layout_can_be_switched_off(self):
        spec = self.far_spec()
        cr.finalize_spec(self.chart, spec, snap=False, layout=False)
        self.assertEqual([c["label_x"] for c in spec["callouts"]], [880, 880, 880])
        self.assertFalse(any("label_from" in c for c in spec["callouts"]))


class RefineFallbackTests(unittest.TestCase):
    def test_failed_refine_pass_keeps_first_review(self):
        from agent.llm import LLMError, ScriptedLLM
        calls = {"n": 0}

        def answer(system, content):
            calls["n"] += 1
            if calls["n"] == 2:
                raise LLMError("Model response was cut off or empty")
            return chart_spec()
        tmp = tempfile.mkdtemp(prefix="refine_")
        try:
            img = Image.open(make_chart(os.path.join(tmp, "c.png")))
            cfg = {"review_refine_passes": 1, "docs_folder": tmp, "knowledge_base_folder": ""}
            out, spec = cr.ChartReviewer(cfg, llm=ScriptedLLM({"chart_review": answer})).review(img)
            self.assertEqual(calls["n"], 2)
            self.assertIn("自我检查轮失败", spec["limitations"])
            self.assertEqual(spec["annotations"][0]["annotation_id"], "H2_001")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_study_material_with_braces_does_not_break_the_prompt(self):
        tmp = tempfile.mkdtemp(prefix="braces_")
        try:
            with open(os.path.join(tmp, "notes.md"), "w", encoding="utf-8") as f:
                f.write("My notes {H2} and {} and {knowledge_x}")
            reviewer = cr.ChartReviewer({"docs_folder": tmp, "knowledge_base_folder": ""}, llm=object())
            self.assertIn("My notes {H2} and {} and {knowledge_x}", reviewer.system)
            self.assertNotIn("{knowledge}", reviewer.system)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
