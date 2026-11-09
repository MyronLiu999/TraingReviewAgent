"""Trade assistant: TAKE / WAIT / NO_TRADE rules, trader's equation, position size, plan chart,
routing of trading questions, and the live-monitoring interfaces (feeds, triggers, monitor loop)."""

import copy
import os
import unittest
from datetime import datetime, timedelta

from PIL import Image

from agent.router import route
from coach import decision as dec
from coach.plan_chart import check_geometry, fill_positions, render_plan
from market.ohlc import Bar
from monitor import CsvFileFeed, ListNotifier, Monitor, ScreenshotFolderFeed, check_plan, make_feed, new_plan_state
from tests.helpers import FixtureCase, decision_answer, scripted

EV = [{"evidence_id": "S1", "authority": "canonical", "excerpt": "high 2 buy above the signal bar",
       "full_text": "high 2 buy above the signal bar", "citation": "[Trends p.12]"}]


def settings(**trading):
    return dec.trading_settings({"trading": trading})


def check(st=None, **over):
    return dec.validate_decision(copy.deepcopy(decision_answer(**over)), EV, None, st or settings())


class TradeRuleTests(unittest.TestCase):
    def test_good_plan_stays_take_with_code_computed_equation(self):
        r, issues = check()
        self.assertEqual(r["recommendation"], "TAKE")
        eq = r["traders_equation"]
        self.assertEqual(eq["reward_risk"], 2.0)
        self.assertAlmostEqual(eq["expectancy_r"], 0.8)          # 0.6 * 2 - 0.4
        self.assertTrue(eq["ok"])
        self.assertEqual(r["runner_rr"], 3.0)                    # (107-101)/(101-99)

    def test_trader_equation_failure_downgrades_take(self):
        r, issues = check(probability="low", target={"logic": "1R", "price": 103.0, "y": 400})
        self.assertEqual(r["recommendation"], "WAIT")
        self.assertFalse(r["traders_equation"]["ok"])
        self.assertTrue(any("trader's equation" in i for i in issues))

    def test_checklist_deal_breaker_and_low_grade_veto_take(self):
        r, issues = check(checklist=[{"item": "位置", "verdict": "fail", "note": "区间中间"}])
        self.assertEqual(r["recommendation"], "WAIT")
        self.assertTrue(any("deal-breaker" in i for i in issues))
        r, _ = check(setup_quality="C")
        self.assertEqual(r["recommendation"], "WAIT")
        r, _ = check(settings(min_setup_quality="C"), setup_quality="C")
        self.assertEqual(r["recommendation"], "TAKE")

    def test_take_needs_a_direction(self):
        r, issues = check(bias="WAIT", wait_for="等 H2 信号K线", entry={"order_type": "stop", "condition": "x",
                                                                          "price": None, "y": None})
        self.assertEqual(r["recommendation"], "WAIT")
        r, _ = check(recommendation="MAYBE")
        self.assertEqual(r["recommendation"], "WAIT")

    def test_runner_on_the_wrong_side_is_removed(self):
        r, issues = check(runner_target={"logic": "x", "price": 100.0, "y": 520})
        self.assertIsNone(r["runner_target"]["price"])
        self.assertTrue(any("runner target" in i for i in issues))

    def test_position_size_from_risk_settings(self):
        st = settings(account_size=10000, risk_per_trade_pct=1)       # $100 per trade
        r, _ = check(st)                                              # MES, 2 points stop -> $10 / contract
        self.assertEqual(r["position"]["contracts"], 10)
        self.assertEqual(r["position"]["risk_usd"], 100.0)
        r, _ = check(settings(account_size=10000, risk_per_trade_pct=1, max_contracts=3))
        self.assertEqual(r["position"]["contracts"], 3)
        r, issues = check(st, chart_read={"symbol": "ES", "timeframe": "5m", "last_price": None, "last_bar": ""})
        self.assertEqual(r["position"]["contracts"], 1)               # 2 points * $50 = $100
        r, issues = check(settings(account_size=5000, risk_per_trade_pct=1),
                          chart_read={"symbol": "ES", "timeframe": "5m", "last_price": None, "last_bar": ""})
        self.assertEqual(r["position"]["contracts"], 0)               # $100 per contract > $50 budget
        self.assertEqual(r["recommendation"], "NO_TRADE")
        r, _ = check()
        self.assertIsNone(r["position"]["contracts"])                 # risk not configured: no size, no veto
        self.assertEqual(r["recommendation"], "TAKE")

    def test_symbol_roots(self):
        pv = dec.DEFAULT_POINT_VALUES
        for text, root in (("MESZ6", "MES"), ("/MES", "MES"), ("MYM 5分钟", "MYM"), ("MES1!", "MES"),
                           ("ESH27 1min", "ES"), ("这根是 H2 吗", None), ("", None)):
            self.assertEqual(dec.symbol_root(text, pv), root, text)
        st = settings(point_values={"SPY": 1})
        self.assertEqual(dec.symbol_root("SPY 15m", st["point_values"]), "SPY")

    def test_prompt_has_playbook_and_risk(self):
        text = dec.build_system(EV, {}, None, True, "screenshot only", settings(account_size=20000))
        for word in ("TRADER'S EQUATION", "NO_TRADE", "signal bar", "risk_budget_usd_per_trade", "200.0"):
            self.assertIn(word, text)

    def test_old_saved_decisions_still_format(self):
        old = decision_answer()
        for k in ("recommendation", "chart_read", "checklist", "runner_target", "signal_bar", "setup_quality"):
            old.pop(k)
        r, _ = dec.validate_decision(old, EV)
        text = dec.format_decision(r, EV, [], {"source": "screenshot"})
        self.assertIn("BIAS", text)


class PlanChartTests(unittest.TestCase):
    def test_draws_lines_and_fills_missing_positions(self):
        r, _ = check()
        img = Image.new("RGB", (1000, 600), (19, 23, 34))
        out, issues = render_plan(img, r, dec.headline(r))
        self.assertEqual(out.size, img.size)
        self.assertEqual(issues, [])
        y = round(600 * 600 / 1000)                                   # stop at grid y=600 -> red line
        px = out.getpixel((900, y))
        self.assertGreater(px[0], 150)
        levels = [{"kind": "entry", "price": 101.0, "y": 500}, {"kind": "stop", "price": 99.0, "y": 600},
                  {"kind": "key", "price": 104.0, "y": None}]
        fill_positions(levels)
        self.assertAlmostEqual(levels[2]["y"], 350)                   # 50 grid units per point

    def test_inconsistent_geometry_is_not_drawn(self):
        levels = [{"kind": "entry", "price": None, "y": 500}, {"kind": "stop", "price": None, "y": 400}]
        issues = check_geometry(levels, "LONG")                       # LONG stop above the entry on screen
        self.assertTrue(issues)
        self.assertIsNone(levels[0]["y"])
        levels = [{"kind": "entry", "price": 101.0, "y": 600}, {"kind": "stop", "price": 99.0, "y": 500}]
        self.assertTrue(fill_positions(levels))                       # higher price lower on the screen


class RoutingTests(unittest.TestCase):
    def test_trading_questions_with_a_chart_go_to_the_trade_assistant(self):
        for text in ("这里能不能做？", "在哪里入场？", "MES 5分钟 怎么做", "止盈放哪", "/trade", "where should I enter"):
            self.assertEqual(route({"image_path": "x.png", "text": text}), "decision", text)
        for text in ("我在高点做空，止损被打", "MYM 5分钟", ""):
            self.assertEqual(route({"image_path": "x.png", "text": text}), "review", text)


def bar(t, o, h, l, c):
    return Bar(datetime(2026, 11, 9, 10, 0) + timedelta(minutes=5 * t), o, h, l, c)


PLAN = {"bias": "LONG", "recommendation": "TAKE", "entry": {"order_type": "stop", "price": 101.0},
        "stop": {"price": 99.0}, "target": {"price": 103.0}, "runner_target": {"price": 105.0}}


class TriggerTests(unittest.TestCase):
    def run_plan(self, plan, bars):
        state = new_plan_state(plan)
        return [e["type"] for e in check_plan(plan, bars, state)], state

    def test_stop_entry_target_and_runner(self):
        ev, st = self.run_plan(PLAN, [bar(0, 100, 100.8, 99.5, 100.5), bar(1, 100.5, 101.5, 100.2, 101.4),
                                      bar(2, 101.4, 103.2, 101.0, 103.0), bar(3, 103, 105.5, 102.5, 105)])
        self.assertEqual(ev, ["entry_triggered", "target_hit", "runner_target_hit"])
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["result_r"], 1.5)                         # half at 1R, half at 2R

    def test_breakeven_after_target_one(self):
        ev, st = self.run_plan(PLAN, [bar(0, 100.5, 101.5, 100.2, 101.4), bar(1, 101.4, 103.2, 101.2, 103.0),
                                      bar(2, 103, 103.1, 100.5, 100.8)])
        self.assertEqual(ev, ["entry_triggered", "target_hit", "breakeven_stop"])
        self.assertEqual(st["result_r"], 0.5)

    def test_setup_cancelled_and_same_bar_stop_first(self):
        ev, st = self.run_plan(PLAN, [bar(0, 100, 100.5, 98.8, 99.0)])
        self.assertEqual(ev, ["cancelled"])
        ev, st = self.run_plan(PLAN, [bar(0, 100.5, 101.5, 100.2, 101.4), bar(1, 101.4, 103.5, 98.5, 100)])
        self.assertEqual(ev, ["entry_triggered", "stop_hit"])
        self.assertEqual(st["result_r"], -1.0)

    def test_short_limit_entry(self):
        plan = {"bias": "SHORT", "recommendation": "WAIT", "entry": {"order_type": "limit", "price": 110.0},
                "stop": {"price": 112.0}, "target": {"price": 106.0}, "runner_target": {"price": 106.0}}
        ev, st = self.run_plan(plan, [bar(0, 109, 110.2, 108.8, 109.5), bar(1, 109.5, 109.6, 105.8, 106)])
        self.assertEqual(ev, ["entry_triggered", "target_hit"])
        self.assertEqual(st["result_r"], 2.0)

    def test_plans_without_prices_are_not_tracked(self):
        plan = copy.deepcopy(PLAN)
        plan["entry"]["price"] = None
        self.assertEqual(new_plan_state(plan)["status"], "inactive")
        self.assertEqual(new_plan_state({**PLAN, "recommendation": "NO_TRADE"})["status"], "inactive")


def write_bars(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write("time,open,high,low,close\n")
        for b in rows:
            f.write(f"{b.time:%Y-%m-%d %H:%M},{b.open},{b.high},{b.low},{b.close}\n")
    os.utime(path, (datetime.now().timestamp(), datetime.now().timestamp() + len(rows)))   # new mtime per write


class MonitorTests(FixtureCase):
    def test_feeds(self):
        path = os.path.join(self.tmp, "bars.csv")
        feed = CsvFileFeed(path, "MES", "5m")
        self.assertIsNone(feed.poll())                                # no file yet
        write_bars(path, [bar(i, 100, 100.5, 99.5, 100) for i in range(5)])
        snap = feed.poll()
        self.assertEqual(snap.as_of, bar(4, 0, 0, 0, 0).time)
        self.assertEqual(snap.new_bars, [])                            # first snapshot: history only
        self.assertIsNone(feed.poll())                                # unchanged file
        write_bars(path, [bar(i, 100, 100.5, 99.5, 100) for i in range(7)])
        self.assertEqual(len(feed.poll().new_bars), 2)
        shots = os.path.join(self.tmp, "shots")
        os.makedirs(shots)
        sf = ScreenshotFolderFeed(shots)
        self.assertIsNone(sf.poll())
        Image.new("RGB", (10, 10)).save(os.path.join(shots, "a.png"))
        self.assertTrue(sf.poll().image_path.endswith("a.png"))
        self.assertIsNone(sf.poll())
        with self.assertRaises(ValueError):
            make_feed({"source": "csv"})
        with self.assertRaises(NotImplementedError):
            make_feed({"source": "broker"})

    def test_monitor_analyses_then_tracks_the_plan(self):
        bars0 = [bar(i, 100, 100.6, 99.6, 100.2) for i in range(30)]
        last = bars0[-1].close
        plan = decision_answer(entry={"order_type": "stop", "condition": "above signal bar", "price": last + 0.8, "y": None},
                               stop={"logic": "below", "price": last - 0.4, "y": None},
                               target={"logic": "2R", "price": last + 3.2, "y": None},     # within 4 ATR of the data
                               runner_target={"logic": "2R", "price": last + 3.2, "y": None})
        agent = self.make_agent(scripted(trade_decision=plan))
        path = os.path.join(self.tmp, "live.csv")
        write_bars(path, bars0)
        note = ListNotifier()
        mon = Monitor(agent, CsvFileFeed(path, "MES", "5m"), note, {"reanalyze_every_bars": 5})
        out = mon.step()
        self.assertEqual(out[0]["type"], "analysis")
        self.assertEqual(mon.state["status"], "pending")
        self.assertIn("MES", note.sent[0]["text"])
        prompt = self.llm.prompt_text()
        self.assertIn("2026-11-09T12:25", prompt)                     # last bar reached the model ...
        calls = len(self.llm.calls)
        write_bars(path, bars0 + [bar(30, 100.2, 101.2, 100.1, 101.1)])
        ev = mon.step()
        self.assertEqual([e["type"] for e in ev], ["entry_triggered"])
        self.assertEqual(len(self.llm.calls), calls)                   # ... and no re-analysis inside a trade
        write_bars(path, bars0 + [bar(30, 100.2, 101.2, 100.1, 101.1), bar(31, 101.1, 105.2, 101, 105)])
        ev = mon.step()
        self.assertEqual(ev[0]["type"], "target_hit")
        self.assertEqual(mon.trades[-1]["status"], "done")
        self.assertEqual(mon.trades[-1]["result_r"], 2.0)
        self.assertEqual(ev[-1]["type"], "analysis")                   # finished plan -> fresh analysis
        self.assertTrue(any("到达目标1" in s["text"] for s in note.sent))
        self.assertTrue(all("不会下单" in s["text"] for s in note.sent))

    def test_model_call_limit(self):
        agent = self.make_agent(scripted(trade_decision=decision_answer(recommendation="NO_TRADE")))
        path = os.path.join(self.tmp, "live.csv")
        rows = [bar(i, 100, 100.6, 99.6, 100.2) for i in range(20)]
        write_bars(path, rows)
        mon = Monitor(agent, CsvFileFeed(path, "MES", "5m"), ListNotifier(), {"max_model_calls_per_hour": 1,
                                                                             "reanalyze_every_bars": 1})
        mon.step()
        write_bars(path, rows + [bar(20, 100, 100.6, 99.6, 100.2)])
        self.assertEqual(mon.step(), [])
        self.assertEqual(len(self.llm.calls), 1)


class TradeFlowTests(FixtureCase):
    def test_screenshot_to_plan_chart(self):
        agent = self.make_agent()
        out = agent.handle({"image_path": self.chart, "text": "MES 5分钟 这里能不能做？在哪里入场？"})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["mode"], "decision")
        self.assertTrue(os.path.isfile(out["image_path"]))
        case = agent.memory.case(out["case_id"])
        self.assertEqual(case["annotated_path"], out["image_path"])
        self.assertEqual(case["symbol"], "MES")
        for word in ("TRADE PLAN", "TRADER'S EQUATION", "CHECKLIST", "MANAGEMENT", "建议入场 TAKE"):
            self.assertIn(word, out["text"])
        self.assertIn("NO_TRADE", self.llm.calls[-1]["system"])
        follow = agent.handle({"text": "为什么止损放在这里？", "session_id": out["session_id"]})
        self.assertEqual(follow["mode"], "discuss")
        self.assertTrue(follow["ok"], follow.get("error"))


if __name__ == "__main__":
    unittest.main()
