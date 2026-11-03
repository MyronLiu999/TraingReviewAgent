"""Trading assistant bot: chart reviews + news alerts, delivered through Telegram.

Send the bot a chart screenshot  -> it replies with an annotated price-action review chart.
News feeds are checked in the background -> important headlines are sent as alerts.

Usage:
    python main.py                      # run forever
    python main.py --once               # one pass over the feeds, then exit
    python main.py --check "headline"   # analyze one piece of text and print the result
"""

import argparse
import html
import io
import logging
import re
import os
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone

import openai

from PIL import Image

from analyzer import Analyzer
from chart_review import ChartReviewer, store_review
from feeds import NewsItem, fetch_all
from store import Store
from telegram_bot import Telegram, format_alert

log = logging.getLogger("news_bot")

HELP = """📊 <b>Chart review 复盘</b>
Send me a chart screenshot (photo, or as a file for full quality).
Add a caption for context, e.g. "MYM 5分钟，我在高点做空" — I'll reply with an annotated review chart.
Caption "/decide" (or "现在做多还是做空？") — LONG / SHORT / WAIT plan instead (analysis only).

<b>Brooks coach 教练</b>
/study 问题 — theory question with book citations, e.g. /study 解释 H2 和两段回调
/explain #3 为什么是 H2？ — explain (or dispute) a mark on the last chart
/ask 你的问题 — keep discussing the last chart (or reply directly to the coach's message)
/outcome 后来涨了 30 点 — record what happened; lessons go to the candidate list
/memory — pending candidate insights;  /approve 5  /reject 5

<b>News commands</b>
/market equity — judge news for stocks
/market gold — judge news for gold
/market both — equity and gold side by side
/market NVDA — any custom market or ticker (several allowed: /market NVDA TSLA)
/threshold 3 — only alert when impact ≥ this (1-5)
/pause, /resume — stop / restart feed alerts
/status — current settings
/summary — net bias over the last 4 hours
Or just paste any headline, tweet or article text and I'll analyze it right away."""


def load_env(path: str = ".env") -> None:
    """Loads .env into the environment. A variable that is already set wins, except obvious
    placeholders (fewer than 3 characters, e.g. a leftover Windows variable), which .env replaces."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            existing = os.environ.get(k)
            if existing is not None and len(existing.strip()) < 3 and len(v) >= 3:
                log.warning("Environment variable %s looks like a placeholder; using the value from .env", k)
                os.environ[k] = v
            else:
                os.environ.setdefault(k, v)


def parse_markets(args: list[str]) -> list[str]:
    out = []
    for a in args:
        a = a.upper().strip(",")
        if a == "BOTH":
            out += ["EQUITY", "GOLD"]
        elif a in ("STOCK", "STOCKS", "EQUITIES"):
            out.append("EQUITY")
        elif a in ("XAU", "XAUUSD"):
            out.append("GOLD")
        elif a:
            out.append(a)
    return list(dict.fromkeys(out))  # dedupe, keep order


class Bot:
    def __init__(self, cfg: dict, tg: Telegram | None, agent=None):
        self.cfg = cfg
        self.tg = tg
        self.store = Store()
        self.analyzer = Analyzer(cfg)
        self._agent = agent
        self.coach_session = None

    @property
    def agent(self):
        """The Brooks coach (created on first use; None if it cannot start)."""
        if self._agent is None:
            try:
                from agent.runtime import CoachAgent
                self._agent = CoachAgent(self.cfg, project_dir=os.path.dirname(os.path.abspath(__file__)))
            except Exception:
                log.exception("Coach unavailable; falling back to the plain chart review")
                self._agent = False
        return self._agent or None

    def coach(self, request: dict) -> dict | None:
        if not self.agent:
            return None
        out = self.agent.handle({**request, "session_id": self.coach_session, "source": "telegram"})
        self.coach_session = out.get("session_id")
        return out

    # ---------- settings (persist across restarts) ----------
    @property
    def markets(self) -> list[str]:
        return self.store.get_setting("markets", self.cfg.get("markets", ["EQUITY"]))

    @property
    def threshold(self) -> int:
        return self.store.get_setting("threshold", self.cfg.get("min_impact_to_alert", 3))

    @property
    def paused(self) -> bool:
        return self.store.get_setting("paused", False)

    def notify(self, text: str) -> None:
        if self.tg:
            self.tg.send(text)
        else:
            print(html.unescape(re.sub(r"<[^>]+>", "", text)), "\n" + "-" * 60)

    # ---------- Telegram messages ----------
    def handle_incoming(self, msg: dict) -> None:
        if msg.get("image"):
            self.review_chart(msg["image"], msg.get("text", ""))
        elif msg.get("text") and msg.get("reply_to_bot") and not msg["text"].startswith("/"):
            self.coach_command("/ask", msg["text"])     # replying to the coach continues the discussion
        elif msg.get("text"):
            self.handle_message(msg["text"])

    def review_chart(self, file_id: str, note: str) -> None:
        decide = note.lower().startswith("/decide")
        self.notify("🎯 收到图表，正在做交易决策分析…" if decide else "📊 收到图表，正在复盘，大约需要 30-120 秒…")
        try:
            chart = Image.open(io.BytesIO(self.tg.download(file_id)))
            if decide:
                note = note[len("/decide"):].strip() or "现在我应该更关注做多还是做空？"
            out = self.coach({"image": chart, "text": note, "note": note,
                              **({"mode": "decision"} if decide else {})})
            if out is None:   # coach unavailable: the original single-call review
                img, spec = ChartReviewer(self.cfg).review(chart, note)
                meta = store_review(chart, img, spec, note, source="telegram")
                analysis = spec.get("analysis", "")
            elif not out["ok"]:
                raise RuntimeError(out["error"])
            elif out["mode"] == "decision":
                self.notify("🎯 <b>交易决策（仅分析，不下单）</b>\n" + html.escape(out["text"]))
                return
            else:
                img, meta, analysis = Image.open(out["image_path"]), out["meta"], out["text"]
        except Exception as e:
            log.exception("Chart review failed")
            self.notify(f"❌ 复盘失败：{html.escape(str(e))}")
            return
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.tg.send_photo(buf.getvalue(), caption="复盘图 Review")
        if self.cfg.get("review_send_full_resolution", True):
            self.tg.send_photo(buf.getvalue(), caption="原图分辨率（可放大查看）", as_file=True)
        self.notify("🧠 <b>复盘分析</b>\n" + html.escape(analysis))
        log.info("Review saved: %s", meta["id"])

    def coach_command(self, cmd: str, arg: str) -> None:
        if cmd in ("/memory", "/approve", "/reject"):
            if not self.agent:
                self.notify("❌ 教练模块不可用")
                return
            mem = self.agent.memory
            if cmd == "/memory":
                rows = mem.candidates("candidate")[:15]
                self.notify("🟡 <b>候选经验</b>（/approve 编号 或 /reject 编号）\n" + "\n".join(
                    f"#{c['id']} [{c['kind']}] {html.escape(c['content'][:150])}" for c in rows)
                    if rows else "没有待处理的候选。")
            elif arg.isdigit():
                try:
                    if cmd == "/approve":
                        self.notify(f"✅ 已批准 → {mem.approve_candidate(int(arg), approved_by='telegram-owner')}")
                    else:
                        mem.reject_candidate(int(arg), "rejected in Telegram")
                        self.notify(f"已拒绝候选 #{arg}")
                except ValueError as e:
                    self.notify(f"❌ {html.escape(str(e))}")
            return
        if not arg:
            self.notify(HELP)
            return
        mode = {"/study": "study", "/ask": None, "/explain": "explain", "/outcome": "outcome"}[cmd]
        self.notify("⏳ 正在检索教材…" if mode == "study" else "⏳ 教练正在思考…")
        out = self.coach({"text": arg, **({"mode": mode} if mode else {})})   # /ask: discuss the last chart, else study
        if out is None:
            self.notify("❌ 教练模块不可用")
        elif not out["ok"]:
            self.notify(f"❌ {html.escape(out['error'])}")
        else:
            self.notify(html.escape(out["text"]))

    def handle_message(self, text: str) -> None:
        if not text.startswith("/"):
            self.check_text(text)
            return
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/study", "/ask", "/explain", "/outcome", "/memory", "/approve", "/reject"):
            rest = text.split(None, 1)
            self.coach_command(cmd, rest[1].strip() if len(rest) > 1 else "")
        elif cmd == "/market" and args:
            markets = parse_markets(args)
            self.store.set_setting("markets", markets)
            self.notify(f"✅ Now analyzing for: <b>{', '.join(markets)}</b>")
        elif cmd == "/threshold" and args and args[0].isdigit() and 1 <= int(args[0]) <= 5:
            self.store.set_setting("threshold", int(args[0]))
            self.notify(f"✅ Alert threshold set to impact ≥ {args[0]}")
        elif cmd == "/pause":
            self.store.set_setting("paused", True)
            self.notify("⏸ Feed alerts paused. You can still paste news to check it.")
        elif cmd == "/resume":
            self.store.set_setting("paused", False)
            self.notify("▶️ Feed alerts resumed.")
        elif cmd == "/status":
            self.notify(f"Markets: <b>{', '.join(self.markets)}</b>\n"
                        f"Threshold: impact ≥ {self.threshold}\n"
                        f"Feed alerts: {'paused' if self.paused else 'on'}\n"
                        f"Feeds: {len(self.cfg.get('feeds', []))}")
        elif cmd == "/summary":
            self.send_summary()
        elif cmd == "/check" and args:
            self.check_text(text.split(None, 1)[1])
        else:
            self.notify(HELP)

    def check_text(self, text: str) -> None:
        item = NewsItem(uid="manual", source="Pasted by user (unverified)", tier=3,
                        title=text[:300], summary=text[300:1500], link="",
                        published=datetime.now(timezone.utc))
        try:
            result = self.analyzer.analyze([item], self.markets)[0]
        except Exception as e:
            log.exception("Manual check failed")
            self.notify(f"❌ Analysis failed: {e}")
            return
        if result is None or not result.relevant or not result.analyses:
            self.notify("⚪ Not market-relevant for " + ", ".join(self.markets)
                        + (f"\n<i>{html.escape(result.summary)}</i>" if result else ""))
            return
        self.notify(format_alert(item, result, result.analyses))

    def send_summary(self) -> None:
        rows = self.store.recent_verdicts(hours=4)
        if not rows:
            self.notify("No relevant news analyzed in the last 4 hours.")
            return
        score = {"POSITIVE": 1, "NEGATIVE": -1}
        lines = ["<b>Net bias, last 4 hours</b> (impact-weighted)"]
        for market in dict.fromkeys(r[0] for r in rows):
            mrows = [r for r in rows if r[0] == market]
            net = sum(score.get(v, 0) * imp for _, v, imp in mrows)
            icon = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
            lines.append(f"{icon} {market}: {net:+d} from {len(mrows)} items")
        self.notify("\n".join(lines))

    # ---------- feed loop ----------
    def poll_feeds(self) -> None:
        items = fetch_all(self.cfg.get("feeds", []))
        unique = {it.uid: it for it in items}
        new_uids = self.store.unseen(list(unique))

        if self.store.is_empty() and self.cfg.get("quiet_first_run", True):
            self.store.mark_seen(list(unique))
            log.info("First run: marked %d existing headlines as seen", len(unique))
            return

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.cfg.get("max_item_age_minutes", 180))
        fresh, old = [], []
        for uid in new_uids:
            it = unique[uid]
            (old if it.published and it.published < cutoff else fresh).append(it)
        self.store.mark_seen([it.uid for it in old])
        if self.paused:
            self.store.mark_seen([it.uid for it in fresh])
            return
        if not fresh:
            return

        fresh.sort(key=lambda it: it.published or datetime.now(timezone.utc))
        log.info("Analyzing %d new headlines", len(fresh))
        size = max(1, int(self.cfg.get("batch_size", 8)))
        markets = self.markets
        for i in range(0, len(fresh), size):
            batch = fresh[i:i + size]
            try:
                results = self.analyzer.analyze(batch, markets)
            except (openai.APIConnectionError, openai.RateLimitError,
                    openai.InternalServerError) as e:
                log.warning("OpenAI temporarily unavailable (%s); will retry next poll", e)
                return  # leave unseen so they are retried
            except Exception:
                log.exception("Batch analysis failed; skipping these headlines")
                self.store.mark_seen([it.uid for it in batch])
                continue

            for item, result in zip(batch, results):
                self.handle_result(item, result)
            self.store.mark_seen([it.uid for it in batch])

    def handle_result(self, item: NewsItem, result) -> None:
        if result is None or not result.relevant:
            self.store.log_verdict(item, {"market": "-", "verdict": "IRRELEVANT", "impact": 0,
                                          "confidence": "-"}, alerted=False)
            return
        alert_neutral = self.cfg.get("alert_on_neutral", False)
        to_alert = [a for a in result.analyses
                    if a.impact >= self.threshold and (alert_neutral or a.verdict != "NEUTRAL")]
        if to_alert:
            self.notify(format_alert(item, result, to_alert))
        for a in result.analyses:
            self.store.log_verdict(item, a.model_dump(), alerted=a in to_alert)

    def run(self, once: bool = False) -> None:
        poll = int(self.cfg.get("poll_seconds", 120))
        next_poll = 0.0
        if self.tg and not once:
            self.notify("🤖 Bot started. Send me a chart screenshot for a review (复盘), "
                        f"or /help for commands. News markets: <b>{', '.join(self.markets)}</b>")
        while True:
            if self.tg:
                for msg in self.tg.incoming():
                    self.handle_incoming(msg)
            if self.cfg.get("enable_news_alerts", True) and time.time() >= next_poll:
                try:
                    self.poll_feeds()
                except Exception:
                    log.exception("Feed poll failed")
                next_poll = time.time() + poll
            if once:
                return
            time.sleep(3)  # check Telegram for commands every few seconds


def main() -> None:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()

    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one feed pass and exit")
    ap.add_argument("--check", metavar="TEXT", help="analyze one piece of text and print it")
    ap.add_argument("--no-telegram", action="store_true", help="print alerts instead of sending")
    args = ap.parse_args()

    with open("config.toml", "rb") as f:
        cfg = tomllib.load(f)

    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    tg = None
    if not args.no_telegram and not args.check:
        if not (token and chat):
            sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (or use --no-telegram)")
        tg = Telegram(token, chat)

    bot = Bot(cfg, tg)
    if args.check:
        bot.check_text(args.check)
    else:
        bot.run(once=args.once)


if __name__ == "__main__":
    main()
