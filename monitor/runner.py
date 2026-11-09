"""The monitoring loop: poll a feed, check the active plan, re-run the trade analysis, alert on changes.

    monitor = Monitor(agent, make_feed(cfg["monitor"]), ConsoleNotifier(), cfg["monitor"])
    monitor.step()            # one poll (tests, schedulers)
    monitor.run_forever()     # loop every poll_seconds until stop_event is set

Cost control: a model call happens only on the first snapshot, after `reanalyze_every_bars` new bars,
after the active plan finished / was cancelled, or for every new screenshot - and never more than
`max_model_calls_per_hour`.
"""

import logging
import threading
import time
from collections import deque

from coach.decision import headline
from monitor.feeds import MarketFeed, Snapshot
from monitor.notifier import Notifier
from monitor.triggers import check_plan, new_plan_state

log = logging.getLogger(__name__)
EVENT_TEXT = {"entry_triggered": "🚀 入场已触发", "stop_hit": "🛑 止损被打", "target_hit": "🎯 到达目标1",
              "runner_target_hit": "🏁 到达目标2",
              "breakeven_stop": "↩️ 剩余仓位保本离场", "cancelled": "✖️ 计划取消（入场前跌破/升破止损）"}
DEFAULT_QUESTION = "实时监控：根据最新收盘的K线，现在能不能做？给出入场、止损、目标，以及是否建议入场。"


class Monitor:
    def __init__(self, agent, feed: MarketFeed, notifier: Notifier, mcfg: dict | None = None,
                 session_id: str | None = None):
        mcfg = mcfg or {}
        self.agent, self.feed, self.notifier = agent, feed, notifier
        self.poll_seconds = float(mcfg.get("poll_seconds", 10))
        self.reanalyze_every = max(1, int(mcfg.get("reanalyze_every_bars", 3)))
        self.max_calls_per_hour = int(mcfg.get("max_model_calls_per_hour", 12))
        self.notify_all = str(mcfg.get("notify", "changes")) == "all"
        self.question = mcfg.get("question") or DEFAULT_QUESTION
        self.session_id = session_id
        self.plan: dict | None = None
        self.plan_case: str | None = None
        self.state: dict = {"status": "inactive"}
        self.bars_since_analysis = 0
        self.calls: deque = deque()
        self.log: list[dict] = []                  # everything that happened (for a future web panel)
        self.trades: list[dict] = []               # finished / cancelled plans with their result in R

    # ---------------------------------------------------------------- one poll
    def step(self) -> list[dict]:
        snap = self.feed.poll()
        if snap is None:
            return []
        out: list[dict] = []
        if self.plan and snap.new_bars:
            was = self.state.get("status")
            for ev in check_plan(self.plan, snap.new_bars, self.state):
                out.append(ev)
                self._alert(f"{EVENT_TEXT.get(ev['type'], ev['type'])} · {snap.symbol} {snap.timeframe} @ {ev['price']}"
                            f"（{ev['time']}）{(' · ' + ev['note']) if ev['note'] else ''}\n计划：{headline(self.plan)}")
            if self.state.get("status") in ("done", "cancelled") and was != self.state["status"]:
                self.trades.append({"case_id": self.plan_case, "bias": self.plan["bias"], **self.state})
        self.bars_since_analysis += len(snap.new_bars) or 1
        if self._should_analyze(snap):
            d = self.analyze(snap)
            if d:
                out.append(d)
        self.log += out
        return out

    def _should_analyze(self, snap: Snapshot) -> bool:
        if self.state.get("status") == "in_trade":
            return False                           # manage the open plan; do not flip it every bar
        due = (self.plan is None or snap.image_path is not None or self.state.get("status") in ("done", "cancelled", "inactive")
               or self.bars_since_analysis >= self.reanalyze_every)
        if not due:
            return False
        now = time.time()
        while self.calls and now - self.calls[0] > 3600:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls_per_hour:
            log.warning("monitor: model call limit (%s/hour) reached, skipping analysis", self.max_calls_per_hour)
            return False
        self.calls.append(now)
        return True

    def analyze(self, snap: Snapshot) -> dict | None:
        req = {"mode": "decision", "text": f"{snap.symbol} {snap.timeframe} {self.question}".strip(),
               "symbol": snap.symbol, "source": "monitor", "session_id": self.session_id}
        if snap.csv:
            req["csv"] = snap.csv
            if snap.as_of:
                req["as_of"] = snap.as_of.isoformat(sep=" ")
        if snap.image_path:
            req["image_path"] = snap.image_path
        out = self.agent.handle(req)
        self.session_id = out.get("session_id") or self.session_id
        self.bars_since_analysis = 0
        if not out.get("ok"):
            log.error("monitor analysis failed: %s", out.get("error"))
            return None
        d = out["decision"]
        old = self.plan
        changed = old is None or (old["bias"], old["recommendation"]) != (d["bias"], d["recommendation"])
        self.plan, self.plan_case = d, out.get("case_id")
        self.state = new_plan_state(d)
        if changed or self.notify_all:
            self._alert(f"📡 {snap.symbol} {snap.timeframe} · {headline(d)}\n{d['conclusion']}"
                        + (f"\n等待：{d['wait_for']}" if d["recommendation"] != "TAKE" and d.get("wait_for") else ""),
                        out.get("image_path"))
        return {"type": "analysis", "case_id": out.get("case_id"), "bias": d["bias"],
                "recommendation": d["recommendation"], "changed": changed}

    def _alert(self, text: str, image_path: str | None = None) -> None:
        self.notifier.send(text + "\n（仅分析，不会下单）", image_path)

    # ---------------------------------------------------------------- loop
    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        log.info("monitor started: %s %s via %s", self.feed.symbol, self.feed.timeframe, self.feed.name)
        while not stop_event.is_set():
            try:
                self.step()
            except Exception:
                log.exception("monitor step failed")
            stop_event.wait(self.poll_seconds)
        self.feed.close()
