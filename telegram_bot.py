"""Minimal Telegram Bot API client: send alerts and read commands (no extra libraries)."""

import html
import logging

import requests

log = logging.getLogger(__name__)

VERDICT_ICON = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "⚪", "MIXED": "🟡"}
TIMING_LABEL = {"immediate": "immediate", "intraday": "intraday", "multi_day": "multi-day"}


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.offset = 0

    def send(self, text: str) -> None:
        try:
            r = requests.post(f"{self.base}/sendMessage", timeout=15, json={
                "chat_id": self.chat_id,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            if not r.ok:
                log.error("Telegram send failed: %s %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.error("Telegram send failed: %s", e)

    def incoming(self) -> list[str]:
        """Returns new message texts sent by the configured chat (others are ignored)."""
        try:
            r = requests.get(f"{self.base}/getUpdates", timeout=15,
                             params={"offset": self.offset, "timeout": 0})
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("Telegram getUpdates failed: %s", e)
            return []
        out = []
        for upd in r.json().get("result", []):
            self.offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue  # only obey the owner's chat
            text = (msg.get("text") or msg.get("caption") or "").strip()
            if text:
                out.append(text)
        return out


def format_alert(item, result, analyses) -> str:
    e = html.escape
    lines = []
    flags = []
    if result.misinformation_risk:
        flags.append("⚠️ POSSIBLE MISINFORMATION — verify before acting")
    if result.stale:
        flags.append("⚠️ STALE — may already be priced in")

    for a in analyses:
        icon = VERDICT_ICON.get(a.verdict, "")
        lines.append(f"<b>{icon} {e(a.verdict)} for {e(a.market)}</b>  |  "
                     f"Impact {a.impact}/5  |  Confidence {e(a.confidence)}")
        lines.append(f"⏱ {TIMING_LABEL.get(a.timing, a.timing)}  |  Priced in: {e(a.priced_in)}")
        for r in a.reasons[:3]:
            lines.append(f"• {e(r)}")
        if a.risks:
            lines.append("<i>Could be wrong if:</i> " + e("; ".join(a.risks[:2])))
        if a.watch:
            lines.append("👀 " + e(", ".join(a.watch[:3])))
        lines.append("")

    head = [f"📰 <b>{e(item.title)}</b>", f"<i>{e(result.summary)}</i>",
            f"Source: {e(item.source)} (Tier {item.tier})", ""]
    tail = []
    if item.link:
        tail.append(f'<a href="{e(item.link, quote=True)}">Open article</a>')
    return "\n".join(flags + head + lines + tail).strip()
