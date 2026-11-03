"""Ongoing discussion: reasoning chains, pushback vs. correction, versioned revisions, follow-up questions."""

import os

from agent.router import route
from tests.helpers import FixtureCase, discuss_answer, scripted


def judgment_revision(system, content):
    r = discuss_answer(system, content)
    r["user_point"] = {"verdict": "agree", "reason": "右边已经两次反转失败"}
    r["revisions"] = [
        {"target": "always_in", "annotation_id": "", "new_value": "unclear", "new_type": "none", "new_status": "candidate",
         "new_target_x": 0, "new_target_y": 0, "reason": "trading range at the right edge"},
        {"target": "decision_bias", "annotation_id": "", "new_value": "maybe", "new_type": "none",
         "new_status": "candidate", "new_target_x": 0, "new_target_y": 0, "reason": "invalid on purpose"},
    ]
    return r


class DiscussionTests(FixtureCase):
    def review(self, agent):
        out = agent.handle({"image_path": self.chart, "text": "MYM 5分钟"})
        self.assertTrue(out["ok"], out.get("error"))
        return out

    def test_follow_up_routes_to_discussion_with_reasoning(self):
        agent = self.make_agent()
        rv = self.review(agent)
        self.assertIn("理由：", rv["text"])                         # every mark shows its reason
        out = agent.handle({"text": "你为什么判断现在是 Always In Long？", "session_id": rv["session_id"]})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["mode"], "discuss")
        for part in ("**回答**", "**我的推理过程**", "观察：", "→ 依据：", "→ 推断：", "**我考虑过的其他解释**",
                     "**什么情况会让我改变判断**", "**把握程度", "🤔 **我想问你**"):
            self.assertIn(part, out["text"])
        self.assertIn("S77", " ".join(v.get("flag", "") for v in out["validation"] if isinstance(v, dict)))
        self.assertEqual(out["follow_up_question"], "你能指出 bar 18 是不是更低高点吗？")

    def test_conversation_history_and_pending_question_reach_the_model(self):
        agent = self.make_agent()
        rv = self.review(agent)
        sid = rv["session_id"]
        agent.handle({"text": "整体逻辑是什么？", "session_id": sid})
        agent.handle({"text": "我看 bar 18 确实是更低高点", "session_id": sid})
        prompt = self.llm.prompt_text()
        self.assertIn("整体逻辑是什么？", prompt)                      # earlier turn of this case
        self.assertIn("你能指出 bar 18 是不是更低高点吗？", prompt)     # the coach's own question
        thread = agent.memory.discussion(rv["case_id"])
        self.assertEqual([m["role"] for m in thread], ["coach", "user", "coach", "user", "coach"])

    def test_bare_pushback_does_not_flip_the_judgment(self):
        agent = self.make_agent()
        rv = self.review(agent)
        out = agent.handle({"text": "#1 你标错了", "session_id": rv["session_id"]})
        self.assertEqual(out["user_point"]["verdict"], "disagree")
        self.assertIn("对你观点的判断：不同意", out["text"])
        self.assertEqual(out["revisions_applied"], [])
        self.assertEqual(agent.memory.annotation(rv["case_id"], "H2_001")["version"], 1)
        self.assertEqual(agent.memory.case(rv["case_id"])["feedback"][0]["text"], "#1 你标错了")

    def test_reasoned_challenge_revises_mark_as_new_version(self):
        agent = self.make_agent()
        rv = self.review(agent)
        out = agent.handle({"text": "我觉得 #1 只是 H1，前面只有一腿", "session_id": rv["session_id"]})
        self.assertEqual(out["user_point"]["verdict"], "agree")
        self.assertEqual(out["revision"]["version"], 2)
        self.assertTrue(os.path.isfile(out["revision"]["image"]))
        self.assertEqual(agent.memory.annotation(rv["case_id"], "H2_001")["type"], "H1")
        self.assertEqual(agent.memory.annotation(rv["case_id"], "H2_001", version=1)["type"], "H2")
        self.assertIn("修改记录", out["text"])

    def test_judgment_revisions_keep_the_original_hypothesis(self):
        agent = self.make_agent(scripted(case_discussion=judgment_revision))
        rv = self.review(agent)
        out = agent.handle({"text": "我觉得右边已经是交易区间了，不该再是 Always In Long", "session_id": rv["session_id"]})
        self.assertTrue(out["ok"], out.get("error"))
        view = agent.memory.current_view(rv["case_id"])
        self.assertEqual(view["always_in"], "unclear")
        self.assertEqual(view["revised_fields"], {"always_in": 1})
        self.assertEqual(agent.memory.case(rv["case_id"])["initial_hypothesis"]["always_in"], "long")
        self.assertTrue(any("invalid bias revision" in v.get("flag", "") for v in out["validation"] if isinstance(v, dict)))

    def test_decision_can_be_questioned(self):
        agent = self.make_agent()
        d = agent.handle({"image_path": self.chart, "text": "现在偏多还是偏空？"})
        self.assertIn("推理过程 REASONING", d["text"])
        self.assertIn("我想问你", d["text"])
        out = agent.handle({"text": "为什么止损放在信号K线下方？", "session_id": d["session_id"]})
        self.assertEqual((out["mode"], out["case_id"]), ("discuss", d["case_id"]))
        self.assertIn("偏多，等 H2 触发", self.llm.prompt_text())       # the decision is part of the context

    def test_routing(self):
        self.assertEqual(route({"text": "为什么这样判断？"}, {"current_case_id": "c1"}), "discuss")
        self.assertEqual(route({"text": "为什么这样判断？", "mode": "study"}, {"current_case_id": "c1"}), "study")
        self.assertEqual(route({"text": "为什么这样判断？"}, {}), "study")

    def test_study_asks_back_and_remembers_the_question(self):
        agent = self.make_agent()
        out = agent.handle({"mode": "study", "text": "解释 H2"})
        self.assertIn("我想问你", out["text"])
        self.assertEqual(agent.memory.session(out["session_id"])["state"]["pending_question"], "你能在自己的图上找出一个 H2 吗？")


class TelegramReplyTests(FixtureCase):
    def test_reply_to_coach_continues_discussion(self):
        from main import Bot
        from tests.test_regression import FakeTelegram
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
            with open(self.chart, "rb") as f:
                tg = FakeTelegram(f.read())
            bot = Bot(self.cfg, tg, agent=self.make_agent())
            bot.handle_incoming({"image": "f", "text": "MYM 5分钟"})
            called = {}
            bot.check_text = lambda text: called.setdefault("news", text)
            bot.handle_incoming({"text": "为什么判断多头？", "reply_to_bot": True})
            self.assertIn("推理过程", tg.sent[-1])
            self.assertNotIn("news", called)
            bot.handle_incoming({"text": "/ask 还有什么其他解释？"})
            self.assertIn("其他解释", tg.sent[-1])
            bot.store.db.close()
        finally:
            os.chdir(cwd)
