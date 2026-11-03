"""The seven acceptance scenarios, end to end through CoachAgent.handle (scripted model)."""

import os

from tests.helpers import FixtureCase, decision_answer, scripted, tree_hashes


class ScenarioTests(FixtureCase):
    def test_1_theory_teaching_with_traceable_sources(self):
        agent = self.make_agent()
        out = agent.handle({"text": "请解释 Al Brooks 的 H2，以及它与普通两段式回调的关系。"})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["mode"], "study")
        self.assertIn("[BROOKS SOURCE]", out["text"])
        self.assertIn("[Trends p.10", out["text"])               # citation built from metadata
        self.assertTrue(any(e["authority"] == "canonical" for e in out["evidence"]))
        fake = [c for c in out["claims"] if c["original_label"] == "BROOKS_SOURCE" and not c["evidence_ids"]]
        self.assertTrue(fake and fake[0]["label"] == "AGENT_HYPOTHESIS")   # the S99 claim was downgraded
        self.assertIn("<source id=\"S1\"", self.llm.prompt_text())         # evidence fenced as data

    def test_2_screenshot_review_draws_on_the_original(self):
        agent = self.make_agent()
        out = agent.handle({"image_path": self.chart, "text": "MYM 5分钟"})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["mode"], "review")
        self.assertTrue(os.path.isfile(out["image_path"]))
        self.assertEqual([a["label"] for a in out["annotations"]], ["#1", "#2", "#3"])
        self.assertEqual(out["annotations"][0]["annotation_id"], "H2_001")
        case = agent.memory.case(out["case_id"])
        self.assertEqual(case["symbol"], "MYM")
        self.assertEqual(len(agent.memory.annotations(out["case_id"])), 3 + 1 + 1)   # callouts + ring + zone

    def test_3_explain_and_dispute_a_mark(self):
        agent = self.make_agent()
        rv = agent.handle({"image_path": self.chart, "text": "MYM 5分钟"})
        sid = rv["session_id"]
        out = agent.handle({"text": "你为什么把 #1 标记为 H2？", "session_id": sid})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual((out["mode"], out["annotation_id"]), ("explain", "H2_001"))
        for heading in ("1. 含义", "2. 为什么标在这里", "3. 支持它的 K 线", "4. 为什么不是相似形态", "5. 交易意义", "6. 教材依据"):
            self.assertIn(heading, out["text"])
        disputed = agent.handle({"text": "#1 标错了吧？我觉得只是 H1", "session_id": sid})
        self.assertEqual(disputed["revision"]["version"], 2)
        history = agent.memory.annotations(rv["case_id"], include_history=True)
        versions = [a for a in history if a["annotation_id"] == "H2_001"]
        self.assertEqual(sorted(a["version"] for a in versions), [1, 2])
        self.assertTrue(next(a for a in versions if a["version"] == 1)["superseded"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "reviews", rv["case_id"], "review.png")))   # v1 kept
        self.assertTrue(os.path.isfile(disputed["revision"]["image"]))
        self.assertEqual(agent.memory.case(rv["case_id"])["feedback"][0]["annotation_id"], "H2_001")

    def test_4_confirmed_learning_record_is_used_across_sessions(self):
        agent = self.make_agent()
        agent.handle({"text": "H2 怎么数？"})                     # session A: agent proposes a candidate
        cand = agent.memory.candidates()[0]
        self.assertEqual(cand["kind"], "learning_difficulty")
        agent.handle({"text": "再讲讲 H2"})                       # still only a candidate: not in the prompt
        self.assertNotIn(cand["content"], self.llm.prompt_text())
        agent.memory.approve_candidate(cand["id"], approved_by="tester")
        agent.handle({"text": "H2 和 H1 的区别？"})              # session C: confirmed record reaches the coach
        self.assertIn(cand["content"], self.llm.prompt_text())
        stats = {s["concept"]: s["times_asked"] for s in agent.memory.concept_stats()}
        self.assertGreaterEqual(stats.get("high 2", 0), 3)

    def test_5_decision_gives_a_clear_bias_without_invented_prices(self):
        llm = scripted(trade_decision=decision_answer(price_readability="unreadable"))
        agent = self.make_agent(llm)
        out = agent.handle({"image_path": self.chart, "text": "现在我应该更关注做多还是做空？请给出具体分析。"})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["mode"], "decision")
        d = out["decision"]
        self.assertIn(d["bias"], ("LONG", "SHORT", "WAIT"))
        self.assertIsNone(d["entry"]["price"])                     # unreadable screenshot -> no numbers
        self.assertIsNone(d["risk_reward"])
        for section in ("BIAS", "MARKET CONTEXT", "PRIMARY SETUP", "ENTRY", "STOP", "TARGET", "RISK / REWARD",
                        "INVALIDATION", "ALTERNATIVE SCENARIO", "BROOKS EVIDENCE", "DATA QUALITY"):
            self.assertIn(section, out["text"])
        self.assertIn("不能下单", out["text"])

    def test_6_user_claim_is_not_written_into_brooks_canon(self):
        before = tree_hashes(self.fx["root"])
        agent = self.make_agent()
        out = agent.handle({"text": "以后请记住，所有 H2 都是高概率买点。"})
        self.assertEqual(out["mode"], "memory")
        self.assertIn("候选", out["text"])
        cands = agent.memory.candidates()
        claim = next(c for c in cands if c["kind"] == "user_claim")
        self.assertEqual((claim["status"], claim["source_type"]), ("candidate", "user"))
        self.assertEqual(agent.memory.profile(), [])
        self.assertEqual(agent.memory.rules(), [])
        self.assertEqual(before, tree_hashes(self.fx["root"]))
        self.assertIn("所有 H2 都是高概率买点", self.llm.prompt_text())   # it was checked against sources

    def test_7_integrity_after_everything(self):
        before = tree_hashes(self.fx["root"])
        agent = self.make_agent()
        rv = agent.handle({"image_path": self.chart, "text": "MYM 5分钟"})
        agent.handle({"text": "为什么 #1 是 H2？", "session_id": rv["session_id"]})
        agent.handle({"text": "以后请记住，H2 都要做"})
        self.assertEqual(before, tree_hashes(self.fx["root"]))
        from knowledge.manifest import is_inside, verify
        self.assertTrue(verify(self.fx["root"])["ok"])
        self.assertFalse(is_inside(agent.memory.path, self.fx["root"]))
        self.assertFalse(is_inside(agent.reviews_dir, self.fx["root"]))
