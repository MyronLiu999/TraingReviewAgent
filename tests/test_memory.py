"""Memory layers: persistence, approval flow, immutability of hypotheses, isolation."""

import os
import shutil
import sqlite3
import tempfile
import unittest

from memory.store import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mem_test_")
        self.path = os.path.join(self.tmp, "memory.db")
        self.mem = MemoryStore(self.path)

    def tearDown(self):
        self.mem.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_persists_across_sessions_and_instances(self):
        s = self.mem.session(None, "web")["session_id"]
        self.mem.update_session(s, current_case_id="c1", current_annotation_id="H2_001")
        self.mem.add_message(s, "user", "hi")
        cid = self.mem.add_candidate("preference", "likes bar-by-bar charts", source_type="agent", source_ref="task:1")
        self.mem.close()
        self.mem = MemoryStore(self.path)
        self.assertEqual(self.mem.session(s)["state"]["current_annotation_id"], "H2_001")
        self.assertEqual(self.mem.messages(s)[0]["content"], "hi")
        self.assertEqual(self.mem.candidates()[0]["id"], cid)

    def test_candidates_need_human_approval(self):
        cid = self.mem.add_candidate("learning_difficulty", "often counts H1 as H2", "seen twice", "agent", "task:9")
        self.assertEqual(self.mem.learning_context()["confirmed_profile"], [])
        with self.assertRaises(PermissionError):
            self.mem.approve_candidate(cid, approved_by="")
        target = self.mem.approve_candidate(cid, approved_by="me")
        self.assertTrue(target.startswith("profile:"))
        prof = self.mem.profile()[0]
        self.assertEqual((prof["source_ref"], prof["confirmed_by"]), (f"candidate:{cid}", "me"))
        with self.assertRaises(ValueError):
            self.mem.approve_candidate(cid, approved_by="me")          # not pending anymore
        rule = self.mem.add_candidate("procedural_rule", "context before pattern")
        self.assertTrue(self.mem.approve_candidate(rule, "me").startswith("procedural_rules:"))
        self.assertIn("context before pattern", self.mem.learning_context()["approved_rules"])

    def test_reject_delete_update(self):
        a = self.mem.add_candidate("reflection", "x")
        b = self.mem.add_candidate("reflection", "y")
        self.mem.reject_candidate(a, "no")
        self.mem.delete_candidate(b)
        self.assertEqual(self.mem.candidates(), [])
        self.assertEqual(self.mem.candidates("rejected")[0]["id"], a)
        pid = self.mem.add_profile("preference", "Chinese with English terms", confirmed_by="me")
        self.mem.update_profile(pid, "中文 + English terms")
        self.assertEqual(self.mem.profile()[0]["content"], "中文 + English terms")
        self.mem.delete_profile(pid)
        self.assertEqual(self.mem.profile(), [])
        with self.assertRaises(PermissionError):
            self.mem.add_profile("preference", "x", confirmed_by="")
        actions = [r["action"] for r in self.mem.audit_log()]
        self.assertIn("profile.delete", actions)

    def test_user_claim_approval_stays_a_user_view(self):
        cid = self.mem.add_candidate("user_claim", "所有 H2 都是高概率买点", source_type="user")
        self.mem.approve_candidate(cid, approved_by="me")
        self.assertEqual(self.mem.profile()[0]["category"], "user_view")

    def test_case_hypothesis_is_immutable_and_outcome_separate(self):
        self.mem.save_case({"case_id": "c1", "mode": "decision", "initial_hypothesis": {"bias": "LONG"},
                            "analysis_timestamp": "2026-11-02T10:00:00"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.mem.db.execute("UPDATE cases SET initial_hypothesis = '{\"bias\": \"SHORT\"}' WHERE case_id='c1'")
        self.mem.db.rollback()
        self.mem.add_outcome("c1", "went up 20 points", "as_expected", {"ok": True})
        self.mem.add_feedback("c1", "nice")
        c = self.mem.case("c1")
        self.assertEqual(c["initial_hypothesis"], {"bias": "LONG"})
        self.assertEqual(c["outcomes"][0]["outcome"], "went up 20 points")
        self.assertEqual(c["feedback"][0]["text"], "nice")
        self.mem.add_review_note("c1", "later note")                      # notes are allowed
        self.assertIn("later note", self.mem.case("c1")["review_notes"])

    def test_annotation_versions_are_kept(self):
        self.mem.save_case({"case_id": "c2", "mode": "review"})
        self.mem.save_annotations("c2", [{"annotation_id": "H2_001", "type": "H2", "status": "candidate"}])
        v = self.mem.revise_annotation("c2", "H2_001", {"type": "H1", "status": "candidate"}, "user disputed")
        self.assertEqual(v, 2)
        self.assertEqual(self.mem.annotation("c2", "H2_001")["type"], "H1")
        self.assertEqual(self.mem.annotation("c2", "H2_001", version=1)["type"], "H2")
        self.assertEqual(len(self.mem.annotations("c2", include_history=True)), 2)
        self.assertEqual(len(self.mem.annotations("c2")), 1)

    def test_context_compression_keeps_pinned_state(self):
        s = self.mem.session()["session_id"]
        self.mem.update_session(s, current_case_id="c9", pending_question="why #3?")
        for i in range(40):
            self.mem.add_message(s, "user", f"question {i} " + "x" * 400)
            self.mem.add_message(s, "assistant", "answer " + "y" * 800)
        ctx = self.mem.context_for(s, char_budget=3000)
        self.assertEqual(ctx["pinned"]["current_case_id"], "c9")
        self.assertLess(sum(len(m["content"]) for m in ctx["recent"]), 3000)
        self.assertTrue(ctx["earlier_user_questions"])
        self.assertIn("question 39", ctx["recent"][-2]["content"])

    def test_concept_counters_are_objective(self):
        self.mem.note_concepts(["High 2", "high 2", "wedge"])
        self.mem.note_concepts(["high 2"])
        stats = {s["concept"]: s for s in self.mem.concept_stats()}
        self.assertEqual(stats["high 2"]["times_asked"], 2)
        self.assertEqual(stats["high 2"]["user_status"], "unrated")
        self.mem.set_concept_status("high 2", "struggling")
        self.assertEqual({s["concept"]: s for s in self.mem.concept_stats()}["high 2"]["user_status"], "struggling")

    def test_layers_are_separate_files(self):
        from agent.harness import TaskStore
        tasks = TaskStore(os.path.join(self.tmp, "agent.db"))
        self.assertNotEqual(os.path.abspath(tasks.db.execute("PRAGMA database_list").fetchone()[2]),
                            os.path.abspath(self.path))
        tables = {r[0] for r in self.mem.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("chunks", tables)          # no knowledge tables in memory
        tasks.close()
