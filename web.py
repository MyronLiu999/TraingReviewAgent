"""Web UI for chart reviews (no extra packages needed).

    python web.py            -> open http://localhost:8000 and log in with APP_PASSWORD from .env

Paste (Ctrl+V), drag in, or choose a chart screenshot, add an optional note, and get an
annotated review chart back. Past reviews are listed on the left. If Telegram is set up in
.env, the Telegram bot (alerts + chart reviews by photo) runs in the background as well.
"""

import base64
import hmac
import html
import io
import json
import logging
import os
import re
import secrets
import sys
import threading
import tomllib
import webbrowser
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from PIL import Image

from chart_review import REVIEWS_DIR, ChartReviewer, list_reviews, store_review
from main import Bot, load_env
from telegram_bot import Telegram

log = logging.getLogger("web")

SESSIONS: set[str] = set()
MAX_UPLOAD = 25 * 1024 * 1024
CFG: dict = {}
TG: Telegram | None = None
ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{4}$")

LOGIN_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · Trading Review Agent</title>
<style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#0e1117;color:#e6e6e6;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
form{background:#161b22;padding:36px 40px;border-radius:12px;border:1px solid #2b3240;width:300px}
h1{font-size:20px;margin:0 0 6px}p{color:#8b949e;font-size:13px;margin:0 0 22px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #2b3240;
background:#0e1117;color:#e6e6e6;font-size:15px}
button{margin-top:14px;width:100%;padding:10px;border:0;border-radius:8px;background:#2f81f7;color:#fff;
font-size:15px;cursor:pointer}.err{color:#ff7b72;font-size:13px;margin-top:10px}
</style></head><body><form method="post" action="/login">
<h1>📊 交易复盘助手</h1><p>Trading Review Agent</p>
<input type="password" name="password" placeholder="密码 Password" autofocus>
<button>登录</button>{error}</form></body></html>"""

APP_PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trading Review Agent</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0e1117;color:#e6e6e6;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
display:flex;height:100vh;overflow:hidden}
aside{width:270px;background:#161b22;border-right:1px solid #2b3240;display:flex;flex-direction:column}
aside h2{font-size:15px;margin:16px 16px 8px;display:flex;justify-content:space-between;align-items:center}
aside h2 a{color:#8b949e;font-size:12px;text-decoration:none;font-weight:normal}
#history{overflow-y:auto;flex:1;padding:0 8px 12px}
.item{display:flex;gap:10px;padding:8px;border-radius:8px;cursor:pointer;align-items:center}
.item:hover,.item.active{background:#21262d}
.item img{width:72px;height:40px;object-fit:cover;border-radius:4px;background:#0e1117}
.item .t{font-size:12px;color:#8b949e}.item .n{font-size:13px;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis;max-width:150px}
main{flex:1;overflow-y:auto;padding:22px 28px}
h1{font-size:20px;margin:0 0 4px}.sub{color:#8b949e;font-size:13px;margin-bottom:18px}
#drop{border:2px dashed #2b3240;border-radius:12px;padding:28px;text-align:center;cursor:pointer;
background:#161b22;transition:.15s}
#drop.over{border-color:#2f81f7;background:#1c2533}
#drop .big{font-size:16px;margin-bottom:6px}#drop .small{color:#8b949e;font-size:13px}
kbd{background:#21262d;border:1px solid #2b3240;border-radius:4px;padding:1px 6px;font-size:12px}
#preview{max-width:100%;max-height:240px;border-radius:8px;margin-top:4px;display:none}
.row{display:flex;gap:10px;margin-top:14px}
#note{flex:1;padding:10px 12px;border-radius:8px;border:1px solid #2b3240;background:#0e1117;color:#e6e6e6;font-size:14px}
button{padding:10px 18px;border:0;border-radius:8px;background:#2f81f7;color:#fff;font-size:14px;cursor:pointer}
button:disabled{background:#30363d;color:#8b949e;cursor:default}
button.ghost{background:#21262d;color:#e6e6e6;border:1px solid #2b3240}
#status{margin-top:14px;color:#8b949e;font-size:14px;min-height:20px}
.spin{display:inline-block;width:14px;height:14px;border:2px solid #8b949e;border-top-color:transparent;
border-radius:50%;animation:s 1s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes s{to{transform:rotate(360deg)}}
#result{display:none;margin-top:22px}
#result img{width:100%;border-radius:8px;border:1px solid #2b3240;cursor:zoom-in}
.card{background:#161b22;border:1px solid #2b3240;border-radius:10px;padding:16px 18px;margin-top:14px;
white-space:pre-wrap;line-height:1.65;font-size:14px}
.card h3{margin:0 0 8px;font-size:15px}
.actions{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}
.meta{color:#8b949e;font-size:13px;margin-top:8px}
.err{color:#ff7b72}
.rule{display:flex;gap:10px;padding:6px 0;border-top:1px solid #21262d;white-space:normal}
.rule:first-child{border-top:0}
.rule .id{font-family:Consolas,monospace;color:#79c0ff;min-width:96px}
.badge{font-size:12px;padding:1px 8px;border-radius:10px;height:fit-content;white-space:nowrap}
.met{background:#1f3b2a;color:#56d364}.partial{background:#3b3320;color:#e3b341}.rejected{background:#3d1f22;color:#ff7b72}
</style></head><body>
<aside><h2>历史复盘 <a href="/logout">退出</a></h2><div id="history"></div></aside>
<main>
  <h1>📊 交易复盘助手 Trading Review Agent</h1>
  <div class="sub">截图后直接 <kbd>Ctrl</kbd>+<kbd>V</kbd> 粘贴（Mac：<kbd>⌘</kbd>+<kbd>V</kbd>），或拖入 / 点击选择图片</div>
  <div id="drop"><div class="big">📋 粘贴、拖入或点击上传行情截图</div>
    <div class="small">建议截图带上右侧价格轴，标注会更准确</div>
    <img id="preview"></div>
  <input type="file" id="file" accept="image/*" hidden>
  <div class="row">
    <input id="note" placeholder="可选说明：品种 / 周期 / 你的交易，例如「MYM 5分钟，我在高点做空，止损被打」">
    <button id="go" disabled>开始复盘</button>
  </div>
  <div id="status"></div>
  <div id="result">
    <a id="full" target="_blank"><img id="out" title="点击查看原图"></a>
    <div class="actions">
      <a id="dl" download><button class="ghost">⬇ 下载复盘图</button></a>
      <button class="ghost" id="tg" style="display:none">📨 发送到 Telegram</button>
    </div>
    <div class="meta" id="meta"></div>
    <div class="card"><h3>🧠 复盘分析</h3><div id="analysis"></div></div>
    <div class="card"><h3>📝 复盘总结</h3><div id="summary"></div></div>
    <div class="card" id="rulesCard"><h3>📐 依据的规则 Rules applied</h3><div id="rules"></div></div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
let imageData = null, current = null;
const TG = __TG__;

function setImage(file){
  if(!file || !file.type.startsWith("image/")) return;
  const r = new FileReader();
  r.onload = () => { imageData = r.result; $("preview").src = imageData; $("preview").style.display="block";
                     $("go").disabled = false; $("status").textContent = "图片已就绪，点击「开始复盘」"; };
  r.readAsDataURL(file);
}
document.addEventListener("paste", e => {
  for (const it of e.clipboardData.items) if (it.type.startsWith("image/")) { setImage(it.getAsFile()); e.preventDefault(); return; }
});
$("drop").onclick = () => $("file").click();
$("file").onchange = e => setImage(e.target.files[0]);
["dragover","dragenter"].forEach(ev => $("drop").addEventListener(ev, e => { e.preventDefault(); $("drop").classList.add("over"); }));
["dragleave","drop"].forEach(ev => $("drop").addEventListener(ev, e => { e.preventDefault(); $("drop").classList.remove("over"); }));
$("drop").addEventListener("drop", e => setImage(e.dataTransfer.files[0]));
$("note").addEventListener("keydown", e => { if (e.key === "Enter" && !$("go").disabled) $("go").click(); });

$("go").onclick = async () => {
  $("go").disabled = true;
  $("status").innerHTML = '<span class="spin"></span>正在复盘，大约需要 30-90 秒…';
  try {
    const res = await fetch("/api/review", {method:"POST", headers:{"Content-Type":"application/json"},
                            body: JSON.stringify({image:imageData, note:$("note").value})});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    $("status").textContent = "✅ 复盘完成";
    show(data); loadHistory();
    $("result").scrollIntoView({behavior:"smooth"});
  } catch (e) {
    $("status").innerHTML = '<span class="err">❌ 复盘失败：' + e.message.replace(/</g,"&lt;") + '</span>';
  }
  $("go").disabled = false;
};

function show(m){
  current = m;
  const src = "/reviews/" + m.id + "/review.png";
  $("out").src = src; $("full").href = src; $("dl").href = src; $("dl").download = m.id + "_review.png";
  $("analysis").textContent = m.analysis || "（无）";
  $("summary").textContent = m.summary || "（无）";
  const rules = m.rules_applied || [];
  $("rulesCard").style.display = rules.length ? "block" : "none";
  const label = {met:"成立", partial:"部分成立", rejected:"不成立"};
  $("rules").innerHTML = "";
  for (const r of rules){
    const row = document.createElement("div"); row.className = "rule";
    row.innerHTML = '<span class="id"></span><span class="badge"></span><span class="txt"></span>';
    row.querySelector(".id").textContent = r.rule_id;
    const b = row.querySelector(".badge"); b.textContent = label[r.status] || r.status; b.classList.add(r.status);
    row.querySelector(".txt").textContent = (r.concept ? r.concept + "：" : "") + r.observation;
    $("rules").appendChild(row);
  }
  $("meta").textContent = m.created.replace("T"," ") + (m.note ? "  ·  " + m.note : "") +
                          (m.source === "telegram" ? "  ·  来自 Telegram" : "");
  $("result").style.display = "block";
  $("tg").style.display = TG ? "inline-block" : "none";
  document.querySelectorAll(".item").forEach(el => el.classList.toggle("active", el.dataset.id === m.id));
}
$("tg").onclick = async () => {
  if (!current) return;
  $("tg").disabled = true;
  const res = await fetch("/api/telegram/" + current.id, {method:"POST"});
  $("tg").textContent = res.ok ? "✅ 已发送" : "❌ 发送失败";
  setTimeout(() => { $("tg").textContent = "📨 发送到 Telegram"; $("tg").disabled = false; }, 2500);
};

async function loadHistory(){
  const list = await (await fetch("/api/reviews")).json();
  $("history").innerHTML = "";
  for (const m of list){
    const d = document.createElement("div"); d.className = "item"; d.dataset.id = m.id;
    d.innerHTML = '<img loading="lazy" src="/reviews/' + m.id + '/review.png"><div><div class="n"></div><div class="t"></div></div>';
    d.querySelector(".n").textContent = m.note || m.description || "复盘";
    d.querySelector(".t").textContent = m.created.replace("T"," ").slice(5,16) + (m.source==="telegram" ? " · TG" : "");
    d.onclick = () => { show(m); window.scrollTo(0,0); };
    $("history").appendChild(d);
  }
  if (current) document.querySelectorAll(".item").forEach(el => el.classList.toggle("active", el.dataset.id === current.id));
}
loadHistory();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    # ---------- helpers ----------
    def _session(self) -> str | None:
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = c.get("sid")
        return sid.value if sid and sid.value in SESSIONS else None

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _html(self, page: str, code: int = 200, extra: dict | None = None):
        self._send(code, page.encode(), "text/html; charset=utf-8", extra)

    def _redirect(self, to: str, extra: dict | None = None):
        self._send(303, b"", "text/plain", {"Location": to, **(extra or {})})

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD:
            raise ValueError("图片太大（最大 25 MB）")
        return self.rfile.read(n)

    # ---------- routes ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/login":
            return self._html(LOGIN_PAGE.replace("{error}", ""))
        if path == "/logout":
            sid = self._session()
            SESSIONS.discard(sid)
            return self._redirect("/login", {"Set-Cookie": "sid=; Max-Age=0; Path=/"})
        if not self._session():
            if path.startswith("/api/") or path.startswith("/reviews/"):
                return self._json(401, {"error": "请先登录"})
            return self._redirect("/login")
        if path == "/":
            return self._html(APP_PAGE.replace("__TG__", "true" if TG else "false"))
        if path == "/api/reviews":
            return self._json(200, list_reviews())
        m = re.match(r"^/reviews/([^/]+)/(review|original)\.png$", path)
        if m and ID_RE.match(m.group(1)):
            f = os.path.join(REVIEWS_DIR, m.group(1), m.group(2) + ".png")
            if os.path.exists(f):
                return self._send(200, open(f, "rb").read(), "image/png")
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/login":
            form = parse_qs(self._body().decode())
            pw = form.get("password", [""])[0]
            if hmac.compare_digest(pw.encode(), os.environ["APP_PASSWORD"].encode()):
                sid = secrets.token_urlsafe(32)
                SESSIONS.add(sid)
                return self._redirect("/", {"Set-Cookie": f"sid={sid}; HttpOnly; SameSite=Strict; Path=/"})
            return self._html(LOGIN_PAGE.replace("{error}", '<div class="err">密码错误</div>'), 401)
        if not self._session():
            return self._json(401, {"error": "请先登录"})
        if path == "/api/review":
            return self._review()
        m = re.match(r"^/api/telegram/([^/]+)$", path)
        if m and ID_RE.match(m.group(1)):
            return self._send_telegram(m.group(1))
        self._json(404, {"error": "not found"})

    def _review(self):
        try:
            data = json.loads(self._body())
            raw = data.get("image") or ""
            if "," in raw:
                raw = raw.split(",", 1)[1]
            chart = Image.open(io.BytesIO(base64.b64decode(raw)))
            chart.load()
        except Exception as e:
            return self._json(400, {"error": f"无法读取图片：{e}"})
        note = (data.get("note") or "").strip()[:500]
        try:
            img, spec = ChartReviewer(CFG).review(chart, note)  # re-reads docs/ each time
        except Exception as e:
            log.exception("Review failed")
            return self._json(500, {"error": str(e)})
        meta = store_review(chart, img, spec, note, source="web")
        meta.pop("spec", None)
        self._json(200, meta)

    def _send_telegram(self, rid: str):
        if not TG:
            return self._json(400, {"error": "Telegram 未设置"})
        folder = os.path.join(REVIEWS_DIR, rid)
        try:
            meta = json.load(open(os.path.join(folder, "meta.json"), encoding="utf-8"))
            TG.send_photo(open(os.path.join(folder, "review.png"), "rb").read(),
                          caption=("复盘图 · " + meta.get("note", ""))[:1000], as_file=True)
            if meta.get("analysis"):
                TG.send("🧠 <b>复盘分析</b>\n" + html.escape(meta["analysis"]))
        except Exception as e:
            return self._json(500, {"error": str(e)})
        self._json(200, {"ok": True})


def start_telegram_bot():
    """Runs the Telegram bot loop in a background thread (Bot is created inside the thread
    because its SQLite connection must stay on one thread)."""
    def run():
        try:
            Bot(CFG, Telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])).run()
        except Exception:
            log.exception("Telegram bot stopped")
    threading.Thread(target=run, daemon=True, name="telegram").start()


def main():
    global CFG, TG
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    with open("config.toml", "rb") as f:
        CFG = tomllib.load(f)

    if not os.environ.get("APP_PASSWORD"):
        sys.exit("请在 .env 里设置 APP_PASSWORD=你的登录密码，然后重新启动。")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("请在 .env 里设置 OPENAI_API_KEY。")
    os.makedirs(REVIEWS_DIR, exist_ok=True)

    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat and "..." not in token:
        TG = Telegram(token, chat)
        start_telegram_bot()
        log.info("Telegram bot running in the background")
    else:
        log.info("Telegram not configured; running the web UI only")

    host = CFG.get("web_host", "127.0.0.1")
    port = int(CFG.get("web_port", 8000))
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://localhost:{port}"
    print(f"\n  ✅ 复盘助手已启动：{url}\n  （关闭这个窗口即可停止）\n")
    if CFG.get("open_browser", True):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
