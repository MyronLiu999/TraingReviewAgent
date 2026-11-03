"""Decision support: validation rules, OHLC facts, no future data, no trading capability."""

import copy
import unittest
from datetime import datetime, timedelta

from coach.decision import validate_decision
from market import ohlc
from tests.helpers import FixtureCase, decision_answer, scripted

EV = [{"evidence_id": "S1", "authority": "canonical", "excerpt": "In a strong bull spike about 60% of pullbacks are bought.",
       "full_text": "In a strong bull spike about 60% of pullbacks are bought. high 2 buy above the signal bar",
       "citation": "[Trends p.12]"}]


def bars_csv(n=60, start=100.0):
    t0 = datetime(2026, 11, 2, 9, 30)
    lines, p = ["time,open,high,low,close"], start
    path = [1.0] * 15 + [-0.6, 0.4, -0.7, -0.5, 0.6, -0.4, -0.6, 0.8, 0.9] + [1.0] * (n - 24)
    for i, mv in enumerate(path[:n]):
        o, c = p, p + mv
        lines.append(f"{(t0 + timedelta(minutes=5 * i)).isoformat(sep=' ')},{o},{max(o, c) + 0.2},{min(o, c) - 0.2},{c}")
        p = c
    return "\n".join(lines)


class ValidationTests(unittest.TestCase):
    def check(self, **over):
        return validate_decision(copy.deepcopy(decision_answer(**over)), EV)

    def test_consistent_long_gets_code_computed_rr(self):
        r, issues = self.check()
        self.assertEqual(r["bias"], "LONG")
        self.assertEqual(r["risk_reward"], 2.0)          # (105-101)/(101-99)
        self.assertEqual(r["execution"], "analysis only - no order was placed or changed")

    def test_inconsistent_prices_are_removed(self):
        r, issues = self.check(stop={"logic": "x", "price": 103.0})
        self.assertIsNone(r["entry"]["price"])
        self.assertIsNone(r["risk_reward"])
        self.assertTrue(any("inconsistent" in i for i in issues))
        r, _ = self.check(bias="SHORT")                  # SHORT with long-style prices
        self.assertIsNone(r["stop"]["price"])

    def test_unreadable_prices_are_never_shown(self):
        r, issues = self.check(price_readability="unreadable")
        self.assertIsNone(r["entry"]["price"])
        self.assertIsNone(r["target"]["price"])

    def test_bias_rules(self):
        r, issues = self.check(bias="MAYBE")
        self.assertEqual(r["bias"], "WAIT")
        r, issues = self.check(invalidation="")
        self.assertEqual(r["bias"], "WAIT")
        r, issues = self.check(bias="WAIT", wait_for="")
        self.assertTrue(any("WAIT without" in i for i in issues))

    def test_probabilities_need_book_support(self):
        r, issues = self.check(conclusion="偏多，成功率 85%，与原书 60% 一致")
        self.assertIn("[未经原书支持的概率已移除]", r["conclusion"])
        self.assertIn("60%", r["conclusion"])
        self.assertNotIn("85%", r["conclusion"])


class OHLCTests(unittest.TestCase):
    def test_no_future_bars_after_as_of(self):
        bars = ohlc.load_csv(bars_csv())
        cut, dropped = ohlc.cut_at(bars, datetime(2026, 11, 2, 11, 0))
        self.assertTrue(all(b.time <= datetime(2026, 11, 2, 11, 0) for b in cut))
        self.assertEqual(dropped, len(bars) - len(cut))
        self.assertGreater(dropped, 0)
        self.assertEqual(ohlc.summarize(cut)["last_time"], cut[-1].time.isoformat())

    def test_h1_h2_counting_follows_the_rules(self):
        bars = ohlc.load_csv(bars_csv())
        labels = [e["label"] for e in ohlc.count_pullback(bars, "bull")]
        self.assertGreaterEqual(labels.count("H1"), 1)
        self.assertIn("H2", labels)
        self.assertLess(labels.index("H1"), labels.index("H2"))
        e = next(x for x in ohlc.count_pullback(bars, "bull") if x["label"] == "H2")
        self.assertEqual(e["signal_bar"], e["entry_bar"] - 1)

    def test_two_rising_bars_are_not_an_h2(self):
        csv = "time,open,high,low,close\n" + "\n".join(
            f"2026-11-02 10:{i:02d},{o},{h},{l},{c}" for i, (o, h, l, c) in enumerate(
                [(10, 11, 9.8, 11), (11, 12, 10.8, 12), (12, 12.1, 10.5, 10.7), (10.7, 11.5, 10.6, 11.4),
                 (11.4, 11.9, 11.3, 11.8), (11.8, 12.0, 11.7, 11.95)]))   # pullback, lower high, H1, rising again
        labels = [e["label"] for e in ohlc.count_pullback(ohlc.load_csv(csv), "bull")]
        self.assertEqual(labels, ["H1"])

    def test_bad_bars_are_rejected(self):
        with self.assertRaises(ValueError):
            ohlc.load_csv("time,open,high,low,close\n2026-11-02 10:00,10,9,8,9.5")

    def test_prices_outside_data_are_dropped(self):
        bars = ohlc.load_csv(bars_csv())
        r, issues = validate_decision(copy.deepcopy(decision_answer(
            entry={"condition": "x", "price": 5000.0})), EV, bars)
        self.assertIsNone(r["entry"]["price"])
        self.assertTrue(any("outside the OHLC data range" in i for i in issues))


class DecisionFlowTests(FixtureCase):
    def test_decision_with_ohlc_uses_only_past_bars(self):
        agent = self.make_agent(scripted(trade_decision=decision_answer(
            entry={"condition": "H2 above signal bar", "price": 120.0},
            stop={"logic": "below signal bar", "price": 118.0}, target={"logic": "2R", "price": 124.0})))
        out = agent.handle({"mode": "decision", "text": "现在做多还是做空？", "csv": bars_csv(), "as_of": "2026-11-02 11:00"})
        self.assertTrue(out["ok"], out.get("error"))
        case = agent.memory.case(out["case_id"])
        self.assertLessEqual(case["market_timestamp"], "2026-11-02T11:00:00")
        self.assertGreater(case["data_quality"]["dropped_future_bars"], 0)
        prompt = self.llm.prompt_text()
        self.assertNotIn("2026-11-02T13:", prompt)          # no later bar reached the model
        self.assertIn("BIAS", out["text"])

    def test_no_tool_can_trade(self):
        agent = self.make_agent()
        perms = {t["permission"] for t in agent.tools.specs()}
        self.assertTrue(perms <= {"read", "llm", "render", "memory_write", "case_write"})
        names = " ".join(t["name"] for t in agent.tools.specs())
        for word in ("order", "trade_execute", "broker", "submit", "cancel", "position"):
            self.assertNotIn(word, names)
        self.assertEqual(len(agent.tools.specs()), 10)
