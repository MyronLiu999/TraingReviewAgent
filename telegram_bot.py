"""Minimal Telegram Bot API client: send alerts and read commands (no extra libraries)."""

import html
import logging
import re

import requests

log = logging.getLogger(__name__)

VERDICT_ICON = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "⚪", "MIXED": "🟡"}
TIMING_LABEL = {"immediate": "immediate", "intraday": "intraday", "multi_day": "multi-day"}
LIMIT = 4000   # Telegram allows 4096 characters per message


def md_to_html(text: str) -> str:
    """Coach answers use **bold** headings; Telegram (parse_mode HTML) needs <b>. Works line by
    line, so a tag never spans a line break and messages can be split at any newline."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text))


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Splits a long message at line breaks (never inside a tag or an &entity;), instead of
    cutting it off, so the end of a long answer (sources, the coach's question) still arrives."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                       # one very long line: cut at a safe spot
            cut = limit
            while cut > limit // 2 and (line.rfind("&", 0, cut) > line.rfind(";", 0, cut)
                                        or line.rfind("<", 0, cut) > line.rfind(">", 0, cut)):
                cut -= 1
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:cut])
            line = line[cut:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur or not parts:
        parts.append(cur)
    return parts


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.offset = 0

    def send(self, text: str) -> None:
        for part in split_message(text):
            try:
                r = requests.post(f"{self.base}/sendMessage", timeout=15, json={
                    "chat_id": self.chat_id,
                    "text": part,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                if not r.ok:
                    log.error("Telegram send failed: %s %s", r.status_code, r.text[:200])
            except requests.RequestException as e:
                log.error("Telegram send failed: %s", e)

    def send_photo(self, image_bytes: bytes, caption: str = "", as_file: bool = False) -> None:
        """Sends an image. as_file=True sends it uncompressed (full resolution)."""
        method, field = ("sendDocument", "document") if as_file else ("sendPhoto", "photo")
        try:
            r = requests.post(f"{self.base}/{method}", timeout=60,
                              data={"chat_id": self.chat_id, "caption": caption[:1000]},
                              files={field: ("review.png", image_bytes, "image/png")})
            if not r.ok:
                log.error("Telegram %s failed: %s %s", method, r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.error("Telegram %s failed: %s", method, e)

    def download(self, file_id: str) -> bytes:
        r = requests.get(f"{self.base}/getFile", params={"file_id": file_id}, timeout=15)
        r.raise_for_status()
        path = r.json()["result"]["file_path"]
        token = self.base.rsplit("/bot", 1)[1]
        f = requests.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=60)
        f.raise_for_status()
        return f.content

    def incoming(self) -> list[dict]:
        """New messages from the configured chat (others are ignored).
        Each is {"text": str, "image": file_id or None, "reply_to_bot": bool}."""
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
            image = None
            if msg.get("photo"):
                image = msg["photo"][-1]["file_id"]  # largest size
            elif (msg.get("document") or {}).get("mime_type", "").startswith("image/"):
                image = msg["document"]["file_id"]  # image sent as a file (full quality)
            text = (msg.get("text") or msg.get("caption") or "").strip()
            reply_to_bot = bool(((msg.get("reply_to_message") or {}).get("from") or {}).get("is_bot"))
            if text or image:
                out.append({"text": text, "image": image, "reply_to_bot": reply_to_bot})
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
