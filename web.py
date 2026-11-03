"""Web UI for the Brooks coach (no extra packages needed).

    python web.py            -> open http://localhost:8000 and log in with APP_PASSWORD from .env

Tabs:
  复盘 Review    paste / drag / choose a chart screenshot -> annotated chart with numbered marks, then an
                 ongoing discussion: ask why, challenge any mark or judgment, answer the coach's questions
  学习 Study     ask any Al Brooks theory question; answers carry [BROOKS SOURCE] citations
  决策 Decision  upload the current chart (+ optional OHLC CSV): LONG / SHORT / WAIT plan
  记忆 Memory    view / approve / reject / delete candidate insights, profile entries and rules
Past reviews are listed on the left. If Telegram is set up in .env, the Telegram bot runs too.
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

from chart_review import REVIEWS_DIR, list_reviews
from main import Bot, load_env
from telegram_bot import Telegram

log = logging.getLogger("web")

SESSIONS: dict[str, str] = {}      # login cookie -> coach session id
MAX_UPLOAD = 25 * 1024 * 1024
CFG: dict = {}
TG: Telegram | None = None
AGENT = None                        # CoachAgent, created in main()
ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{4}$")
IMG_RE = re.compile(r"^/reviews/([^/]+)/(review|original|review_v\d{1,3})\.png$")
FIG_RE = re.compile(r"^/figures/(book_\d{2}_p\d{1,5}\.png)$")

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
h1{font-size:20px;margin:0 0 4px}.sub{color:#8b949e;font-size:13px;margin-bottom:14px}
.tabs{display:flex;gap:6px;margin:10px 0 18px;border-bottom:1px solid #2b3240}
.tab{padding:8px 16px;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;font-size:14px}
.tab.on{color:#e6e6e6;border-color:#2f81f7}
.pane{display:none}.pane.on{display:block}
.drop{border:2px dashed #2b3240;border-radius:12px;padding:24px;text-align:center;cursor:pointer;background:#161b22}
.drop.over{border-color:#2f81f7;background:#1c2533}
.drop .big{font-size:16px;margin-bottom:6px}.drop .small{color:#8b949e;font-size:13px}
kbd{background:#21262d;border:1px solid #2b3240;border-radius:4px;padding:1px 6px;font-size:12px}
.preview{max-width:100%;max-height:220px;border-radius:8px;margin-top:4px;display:none}
.row{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}
input.txt,textarea,select{flex:1;padding:10px 12px;border-radius:8px;border:1px solid #2b3240;background:#0e1117;
color:#e6e6e6;font-size:14px;font-family:inherit}
textarea{min-height:70px;width:100%}
button{padding:10px 18px;border:0;border-radius:8px;background:#2f81f7;color:#fff;font-size:14px;cursor:pointer}
button:disabled{background:#30363d;color:#8b949e;cursor:default}
button.ghost{background:#21262d;color:#e6e6e6;border:1px solid #2b3240}
button.sm{padding:4px 10px;font-size:12px}
.status{margin-top:12px;color:#8b949e;font-size:14px;min-height:20px}
.spin{display:inline-block;width:14px;height:14px;border:2px solid #8b949e;border-top-color:transparent;
border-radius:50%;animation:s 1s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes s{to{transform:rotate(360deg)}}
#result{display:none;margin-top:20px}
#result img{width:100%;border-radius:8px;border:1px solid #2b3240;cursor:zoom-in}
.card{background:#161b22;border:1px solid #2b3240;border-radius:10px;padding:16px 18px;margin-top:14px;
white-space:pre-wrap;line-height:1.65;font-size:14px}
.card h3{margin:0 0 8px;font-size:15px;white-space:normal}
.actions{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}
.meta{color:#8b949e;font-size:13px;margin-top:8px}
.err{color:#ff7b72}
.marks{display:flex;flex-wrap:wrap;gap:8px;white-space:normal}
.mark{background:#21262d;border:1px solid #2b3240;border-radius:8px;padding:6px 10px;font-size:13px;cursor:pointer}
.mark:hover{border-color:#2f81f7}.mark .st{color:#8b949e;font-size:11px}
table{width:100%;border-collapse:collapse;font-size:13px;white-space:normal}
td,th{border-bottom:1px solid #2b3240;padding:6px;text-align:left;vertical-align:top}
.figs img{max-width:48%;margin:6px 1% 0 0;border-radius:6px;border:1px solid #2b3240}
.thread{display:flex;flex-direction:column;gap:10px;white-space:normal;max-height:none}
.msg{padding:10px 14px;border-radius:10px;white-space:pre-wrap;line-height:1.6;font-size:14px;max-width:92%}
.msg.user{align-self:flex-end;background:#1f3a5f;border:1px solid #2f81f7}
.msg.coach{align-self:flex-start;background:#0e1117;border:1px solid #2b3240}
.msg .who{font-size:11px;color:#8b949e;margin-bottom:4px}
.quick{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.quick button{padding:4px 10px;font-size:12px;background:#21262d;border:1px solid #2b3240;color:#e6e6e6}
</style></head><body>
<aside><h2>历史复盘 <a href="/logout">退出</a></h2><div id="history"></div></aside>
<main>
  <h1>📊 Al Brooks 交易教练 Trading Coach</h1>
  <div class="sub">复盘标注 · 理论学习 · 交易决策支持（仅分析，不下单） · 所有理论结论标注来源</div>
  <div class="tabs">
    <div class="tab on" data-p="review">复盘 Review</div><div class="tab" data-p="study">学习 Study</div>
    <div class="tab" data-p="decision">决策 Decision</div><div class="tab" data-p="memory">记忆 Memory</div>
  </div>

  <section class="pane on" id="p-review">
    <div class="drop" id="drop"><div class="big">📋 粘贴（<kbd>Ctrl</kbd>+<kbd>V</kbd>）、拖入或点击上传行情截图</div>
      <div class="small">建议截图带上右侧价格轴，标注会更准确</div><img class="preview" id="preview"></div>
    <input type="file" id="file" accept="image/*" hidden>
    <div class="row">
      <input class="txt" id="note" placeholder="可选说明：品种 / 周期 / 你的交易，例如「MYM 5分钟，我在高点做空，止损被打」">
      <button id="go" disabled>开始复盘</button>
    </div>
    <div class="status" id="status"></div>
    <div id="result">
      <a id="full" target="_blank"><img id="out" title="点击查看原图"></a>
      <div class="actions">
        <a id="dl" download><button class="ghost">⬇ 下载复盘图</button></a>
        <button class="ghost" id="tg" style="display:none">📨 发送到 Telegram</button>
      </div>
      <div class="meta" id="meta"></div>
      <div class="card"><h3>🔖 图上标注（点击让教练解释；不同意就直接说）</h3><div class="marks" id="marks"></div></div>
      <div class="card"><h3>💬 和教练讨论这张图（持续对话，教练会给出推理过程，也会反问你）</h3>
        <div class="thread" id="thread"></div>
        <div class="quick" id="quick">
          <button data-q="你判断的整体逻辑是什么？一步步说明。">整体逻辑</button>
          <button data-q="为什么你判断现在是这个 Always In 方向？">为什么这个方向</button>
          <button data-q="还有哪些其他解释？为什么你没有选？">其他解释</button>
          <button data-q="什么情况会让你改变这个判断？">什么会让你改判</button>
          <button data-q="如果是 Brooks，他在这张图上会关注哪些交易机会？">Brooks 会怎么做</button>
        </div>
        <div class="row"><textarea id="ask" placeholder="追问或质疑，例如「为什么 #3 是 H2 而不是 H1？」「我觉得右边已经是交易区间了」「#2 标错了，因为…」（Ctrl+Enter 发送）"></textarea></div>
        <div class="row"><button id="askgo">发送</button></div><div class="status" id="askstatus"></div></div>
      <div class="card"><h3>🧠 复盘分析</h3><div id="analysis"></div></div>
      <div class="card"><h3>📝 复盘总结</h3><div id="summary"></div></div>
      <div class="card"><h3>📈 后续结果（事后复盘，不会修改当时的判断）</h3>
        <div class="row"><input class="txt" id="outcome" placeholder="后来发生了什么？例如「H2 入场后涨了 30 点，到了前高」">
        <button class="ghost" id="outgo">记录结果并反思</button></div><div id="outtext"></div></div>
    </div>
  </section>

  <section class="pane" id="p-study">
    <div class="card"><h3>📖 和教练讨论理论（连续对话：可以追问、反驳、回答它的提问）</h3>
      <div class="thread" id="sthread"></div><div class="figs" id="figs"></div></div>
    <textarea id="q" placeholder="例如：请解释 Al Brooks 的 H2，以及它与普通两段式回调的关系。（Ctrl+Enter 发送）"></textarea>
    <div class="row"><select id="depth"><option value="detailed">详细讲解</option><option value="brief">简短回答</option>
      <option value="bar_by_bar">逐根K线</option><option value="exercise">给我练习</option></select>
      <button id="qgo">发送</button></div>
    <div class="status" id="qstatus"></div>
  </section>

  <section class="pane" id="p-decision">
    <div class="drop" id="ddrop"><div class="big">📋 粘贴或上传当前行情截图</div>
      <div class="small">可选：下方粘贴 OHLC CSV（time,open,high,low,close），价格会以数据为准</div><img class="preview" id="dpreview"></div>
    <input type="file" id="dfile" accept="image/*" hidden>
    <div class="row"><input class="txt" id="dq" value="现在我应该更关注做多还是做空？请给出具体分析。"></div>
    <div class="row"><textarea id="csv" placeholder="可选 OHLC CSV"></textarea></div>
    <div class="row"><input class="txt" id="asof" placeholder="可选：分析时点 as_of，例如 2026-11-02 14:30（之后的K线会被丢弃）">
      <button id="dgo">分析</button></div>
    <div class="status" id="dstatus"></div>
    <div class="card" id="dcard" style="display:none"><h3>💬 决策讨论（可以质疑每一步：方向、入场、止损、目标）</h3>
      <div class="thread" id="dthread"></div>
      <div class="row"><textarea id="dask" placeholder="例如「为什么止损放在信号K线下方而不是区间下沿？」「我觉得这里应该偏空」（Ctrl+Enter 发送）"></textarea></div>
      <div class="row"><button id="daskgo">发送</button></div><div class="status" id="daskstatus"></div></div>
  </section>

  <section class="pane" id="p-memory">
    <div class="card"><h3>🟡 候选经验（需你批准才生效；不会进入 Brooks 原典）</h3><div id="cands"></div></div>
    <div class="card"><h3>✅ 已确认的学习记录</h3><div id="profile"></div>
      <div class="row"><input class="txt" id="newprof" placeholder="添加一条（如：我常把 H1 当成 H2）">
      <select id="newcat"><option value="learning_difficulty">学习难点</option><option value="preference">偏好</option></select>
      <button class="ghost" id="addprof">添加</button></div></div>
    <div class="card"><h3>📏 我的分析规则（procedural）</h3><div id="rules"></div>
      <div class="row"><input class="txt" id="newrule" placeholder="如：先判断市场背景，再识别形态"><button class="ghost" id="addrule">添加</button></div></div>
    <div class="card"><h3>📊 概念统计（客观计数）</h3><div id="concepts"></div></div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
let imageData = null, dImage = null, current = null;
const TG = __TG__;
async function api(path, body){
  const res = await fetch(path, {method: body===undefined ? "GET":"POST", headers:{"Content-Type":"application/json"},
                                 body: body===undefined ? undefined : JSON.stringify(body)});
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
  return data;
}
const busy = (el, msg) => el.innerHTML = '<span class="spin"></span>' + msg;
function bubble(box, role, text){
  const d = document.createElement("div"); d.className = "msg " + (role === "user" ? "user" : "coach");
  d.innerHTML = '<div class="who">' + (role === "user" ? "你" : "教练") + "</div>"; d.appendChild(document.createTextNode(text));
  $(box).appendChild(d); d.scrollIntoView({behavior:"smooth", block:"end"});
}
function ctrlEnter(id, btn){ $(id).addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $(btn).click(); }); }
async function loadThread(caseId, box){
  $(box).innerHTML = "";
  try { const c = await api("/api/case/" + caseId);
        for (const m of (c.discussion || [])) bubble(box, m.role, m.content);
  } catch (e) { /* older reviews have no saved discussion */ }
}
async function discuss(caseId, text, annId, box, statusEl){
  bubble(box, "user", text); busy($(statusEl), "教练正在重新看图并组织理由…");
  try { const d = await api("/api/discuss", {case_id: caseId, annotation_id: annId, text});
        $(statusEl).textContent = ""; bubble(box, "coach", d.text);
        if (d.revision && current && current.id === caseId) { current.latest_image = d.revision.image_name;
          $("out").src = "/reviews/" + caseId + "/" + d.revision.image_name; }
        return d;
  } catch (e) { fail($(statusEl), e); }
}
const fail = (el, e) => el.innerHTML = '<span class="err">❌ ' + esc(e.message) + '</span>';

document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x === t));
  document.querySelectorAll(".pane").forEach(p => p.classList.toggle("on", p.id === "p-" + t.dataset.p));
  if (t.dataset.p === "memory") loadMemory();
  if (t.dataset.p === "study") loadStudy();
});

function readImage(file, cb){ if(!file || !file.type.startsWith("image/")) return;
  const r = new FileReader(); r.onload = () => cb(r.result); r.readAsDataURL(file); }
function setReview(d){ imageData = d; $("preview").src = d; $("preview").style.display="block"; $("go").disabled = false;
  $("status").textContent = "图片已就绪，点击「开始复盘」"; }
function setDecision(d){ dImage = d; $("dpreview").src = d; $("dpreview").style.display="block"; }
document.addEventListener("paste", e => {
  for (const it of e.clipboardData.items) if (it.type.startsWith("image/")) {
    const onDecision = $("p-decision").classList.contains("on");
    readImage(it.getAsFile(), onDecision ? setDecision : setReview); e.preventDefault(); return; }
});
function wireDrop(drop, file, cb){
  drop.onclick = () => file.click(); file.onchange = e => readImage(e.target.files[0], cb);
  ["dragover","dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => readImage(e.dataTransfer.files[0], cb));
}
wireDrop($("drop"), $("file"), setReview); wireDrop($("ddrop"), $("dfile"), setDecision);
$("note").addEventListener("keydown", e => { if (e.key === "Enter" && !$("go").disabled) $("go").click(); });

$("go").onclick = async () => {
  $("go").disabled = true; busy($("status"), "正在复盘，大约需要 30-120 秒…");
  try { const d = await api("/api/review", {image:imageData, note:$("note").value});
        $("status").textContent = "✅ 复盘完成"; show(d.meta); loadHistory(); $("result").scrollIntoView({behavior:"smooth"});
  } catch (e) { fail($("status"), e); }
  $("go").disabled = false;
};

function show(m){
  current = m;
  const src = "/reviews/" + m.id + "/" + (m.latest_image || "review.png");
  $("out").src = src; $("full").href = src; $("dl").href = src; $("dl").download = m.id + "_review.png";
  $("analysis").textContent = m.analysis || "（无）";
  $("summary").textContent = m.summary || "（无）";
  $("meta").textContent = m.created.replace("T"," ") + (m.note ? "  ·  " + m.note : "") +
     (m.source === "telegram" ? "  ·  来自 Telegram" : "") + (m.mode === "decision" ? "  ·  决策分析" : "");
  $("marks").innerHTML = "";
  for (const a of (m.annotations || [])) {
    const b = document.createElement("div"); b.className = "mark";
    b.innerHTML = "<b>" + esc(a.label) + "</b> " + esc((a.text||"").replace("\n"," ")) + ' <span class="st">' + esc(a.annotation_id) + " · " + esc(a.status) + "</span>";
    b.title = "点击：让教练解释这个标注";
    b.onclick = () => discuss(current.id, "请解释 " + a.label + "（" + (a.text||"").replace("\n"," ") + "）这个标注：为什么标在这里？", a.annotation_id, "thread", "askstatus");
    $("marks").appendChild(b);
  }
  if (!(m.annotations||[]).length) $("marks").textContent = "（这条记录没有可追问的标注）";
  $("outtext").textContent = ""; loadThread(m.id, "thread");
  $("result").style.display = "block";
  $("tg").style.display = TG ? "inline-block" : "none";
  document.querySelectorAll(".item").forEach(el => el.classList.toggle("active", el.dataset.id === m.id));
}
$("askgo").onclick = async () => { const t = $("ask").value.trim(); if (!t || !current) return; $("ask").value = "";
  $("askgo").disabled = true; await discuss(current.id, t, null, "thread", "askstatus"); $("askgo").disabled = false; };
document.querySelectorAll("#quick button").forEach(b => b.onclick = () => current && discuss(current.id, b.dataset.q, null, "thread", "askstatus"));
ctrlEnter("ask", "askgo");
$("outgo").onclick = async () => {
  if (!current || !$("outcome").value.trim()) return; busy($("outtext"), "正在对比原判断与结果…");
  try { const d = await api("/api/outcome", {case_id: current.id, text: $("outcome").value}); $("outtext").textContent = d.text; }
  catch (e) { fail($("outtext"), e); }
};
$("tg").onclick = async () => {
  if (!current) return; $("tg").disabled = true;
  const res = await fetch("/api/telegram/" + current.id, {method:"POST"});
  $("tg").textContent = res.ok ? "✅ 已发送" : "❌ 发送失败";
  setTimeout(() => { $("tg").textContent = "📨 发送到 Telegram"; $("tg").disabled = false; }, 2500);
};

let studyLoaded = false;
async function loadStudy(){
  if (studyLoaded) return; studyLoaded = true;
  try { const h = await api("/api/study/history"); for (const m of h.messages) bubble("sthread", m.role, m.content); } catch (e) {}
}
$("qgo").onclick = async () => {
  const q = $("q").value.trim(); if (!q) return; $("q").value = ""; $("qgo").disabled = true;
  bubble("sthread", "user", q); busy($("qstatus"), "正在检索教材并组织回答…");
  try { const d = await api("/api/study", {text:q, depth:$("depth").value});
        $("qstatus").textContent = ""; bubble("sthread", "coach", d.text);
        $("figs").innerHTML = (d.figures||[]).map(f => '<img title="' + esc(f.evidence_id) + ' 原书图页" src="/figures/' + f.name + '">').join("");
  } catch (e) { fail($("qstatus"), e); }
  $("qgo").disabled = false;
};
ctrlEnter("q", "qgo");

$("dgo").onclick = async () => {
  $("dgo").disabled = true; busy($("dstatus"), "正在分析当前行情…");
  try { const d = await api("/api/decision", {image:dImage, text:$("dq").value, csv:$("csv").value, as_of:$("asof").value});
        $("dstatus").textContent = ""; $("dcard").style.display = "block"; window.dCase = d.case_id;
        $("dthread").innerHTML = ""; bubble("dthread", "user", $("dq").value); bubble("dthread", "coach", d.text); loadHistory();
  } catch (e) { fail($("dstatus"), e); }
  $("dgo").disabled = false;
};
$("daskgo").onclick = async () => { const t = $("dask").value.trim(); if (!t || !window.dCase) return; $("dask").value = "";
  $("daskgo").disabled = true; await discuss(window.dCase, t, null, "dthread", "daskstatus"); $("daskgo").disabled = false; };
ctrlEnter("dask", "daskgo");

async function memAct(path, body){ try { await api(path, body || {}); loadMemory(); } catch (e) { alert(e.message); } }
async function loadMemory(){
  const m = await api("/api/memory");
  $("cands").innerHTML = m.candidates.length ? "<table>" + m.candidates.map(c => "<tr><td>#" + c.id + "</td><td>" + esc(c.kind) +
     "</td><td>" + esc(c.content) + '<div class="meta">' + esc(c.source_type) + " · " + esc(c.source_ref) + " · " + esc(c.rationale) +
     '</div></td><td><button class="sm" onclick="memAct(\'/api/memory/candidate/' + c.id + '/approve\')">批准</button> ' +
     '<button class="sm ghost" onclick="memAct(\'/api/memory/candidate/' + c.id + '/reject\')">拒绝</button> ' +
     '<button class="sm ghost" onclick="memAct(\'/api/memory/candidate/' + c.id + '/delete\')">删除</button></td></tr>').join("") + "</table>" : "（没有待处理的候选）";
  $("profile").innerHTML = m.profile.length ? "<table>" + m.profile.map(p => "<tr><td>" + esc(p.category) + "</td><td>" + esc(p.content) +
     '<div class="meta">来源 ' + esc(p.source_type) + " " + esc(p.source_ref||"") + " · 确认人 " + esc(p.confirmed_by) + '</div></td><td>' +
     '<button class="sm ghost" onclick="editProf(' + p.id + ')">修改</button> <button class="sm ghost" onclick="memAct(\'/api/memory/profile/' + p.id + '/delete\')">删除</button></td></tr>').join("") + "</table>" : "（还没有）";
  $("rules").innerHTML = m.rules.length ? "<table>" + m.rules.map(r => "<tr><td>" + esc(r.rule) + '</td><td><button class="sm ghost" onclick="memAct(\'/api/memory/rule/' + r.id + '/delete\')">删除</button></td></tr>').join("") + "</table>" : "（还没有）";
  $("concepts").innerHTML = m.concepts.length ? "<table><tr><th>概念</th><th>提问次数</th><th>练习次数</th><th>你的评估</th></tr>" + m.concepts.map(c => "<tr><td>" + esc(c.concept) + "</td><td>" + c.times_asked + "</td><td>" + c.times_practised + "</td><td>" + esc(c.user_status) + "</td></tr>").join("") + "</table>" : "（还没有）";
}
function editProf(id){ const t = prompt("修改内容："); if (t) memAct("/api/memory/profile/" + id + "/update", {content:t}); }
$("addprof").onclick = () => { const t = $("newprof").value.trim(); if (t) memAct("/api/memory/profile", {content:t, category:$("newcat").value}).then(() => $("newprof").value = ""); };
$("addrule").onclick = () => { const t = $("newrule").value.trim(); if (t) memAct("/api/memory/rule", {rule:t}).then(() => $("newrule").value = ""); };

async function loadHistory(){
  const list = await (await fetch("/api/reviews")).json();
  $("history").innerHTML = "";
  for (const m of list){
    const d = document.createElement("div"); d.className = "item"; d.dataset.id = m.id;
    d.innerHTML = '<img loading="lazy" src="/reviews/' + m.id + '/review.png"><div><div class="n"></div><div class="t"></div></div>';
    d.querySelector(".n").textContent = (m.mode === "decision" ? "🎯 " : "") + (m.note || m.description || "复盘");
    d.querySelector(".t").textContent = m.created.replace("T"," ").slice(5,16) + (m.source==="telegram" ? " · TG" : "");
    d.onclick = () => { document.querySelector('.tab[data-p="review"]').click(); show(m); window.scrollTo(0,0); };
    $("history").appendChild(d);
  }
  if (current) document.querySelectorAll(".item").forEach(el => el.classList.toggle("active", el.dataset.id === current.id));
}
loadHistory();
</script></body></html>"""


def decode_image(raw: str) -> Image.Image:
    if "," in raw:
        raw = raw.split(",", 1)[1]
    img = Image.open(io.BytesIO(base64.b64decode(raw)))
    img.load()
    return img


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    # ---------- helpers ----------
    def _session(self) -> str | None:
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = c.get("sid")
        return sid.value if sid and sid.value in SESSIONS else None

    def _coach_session(self) -> str:
        return SESSIONS[self._session()]

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
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

    def _html(self, page: str, code: int = 200, extra: dict | None = None):
        self._send(code, page.encode(), "text/html; charset=utf-8", extra)

    def _redirect(self, to: str, extra: dict | None = None):
        self._send(303, b"", "text/plain", {"Location": to, **(extra or {})})

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD:
            raise ValueError("图片太大（最大 25 MB）")
        return self.rfile.read(n)

    def _data(self) -> dict:
        raw = self._body()
        return json.loads(raw) if raw else {}

    # ---------- routes ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/login":
            return self._html(LOGIN_PAGE.replace("{error}", ""))
        if path == "/logout":
            SESSIONS.pop(self._session() or "", None)
            return self._redirect("/login", {"Set-Cookie": "sid=; Max-Age=0; Path=/"})
        if not self._session():
            if path.startswith(("/api/", "/reviews/", "/figures/")):
                return self._json(401, {"error": "请先登录"})
            return self._redirect("/login")
        if path == "/":
            return self._html(APP_PAGE.replace("__TG__", "true" if TG else "false"))
        if path == "/api/reviews":
            return self._json(200, list_reviews())
        if path == "/api/memory":
            mem = AGENT.memory
            return self._json(200, {"candidates": mem.candidates("candidate"), "profile": mem.profile(),
                                    "rules": mem.rules(), "concepts": mem.concept_stats(), "cases": mem.cases(30)})
        if path == "/api/study/history":
            msgs = [{"role": "user" if m["role"] == "user" else "coach", "content": m["content"]}
                    for m in AGENT.memory.messages(self._coach_session(), 40) if m["mode"] in ("study", "memory")]
            return self._json(200, {"messages": msgs})
        m = re.match(r"^/api/case/([^/]+)$", path)
        if m and ID_RE.match(m.group(1)):
            mem, cid = AGENT.memory, m.group(1)
            case = mem.case(cid)
            if not case:
                return self._json(404, {"error": "not found"})
            return self._json(200, {**case, "annotations": mem.annotations(cid, include_history=True),
                                    "discussion": mem.discussion(cid, 60), "current_view": mem.current_view(cid),
                                    "revisions": mem.case_revisions(cid)})
        m = IMG_RE.match(path)
        if m and ID_RE.match(m.group(1)):
            f = os.path.join(REVIEWS_DIR, m.group(1), m.group(2) + ".png")
            if os.path.exists(f):
                return self._send(200, open(f, "rb").read(), "image/png")
        m = FIG_RE.match(path)
        if m and AGENT:
            f = os.path.join(AGENT.data_dir, "figure_cache", m.group(1))
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
                SESSIONS[sid] = AGENT.memory.session(None, "web")["session_id"] if AGENT else ""
                return self._redirect("/", {"Set-Cookie": f"sid={sid}; HttpOnly; SameSite=Strict; Path=/"})
            return self._html(LOGIN_PAGE.replace("{error}", '<div class="err">密码错误</div>'), 401)
        if not self._session():
            return self._json(401, {"error": "请先登录"})
        try:
            data = self._data() if not path.startswith("/api/telegram/") else {}
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})
        if path == "/api/review":
            return self._review(data)
        if path in ("/api/study", "/api/decision", "/api/explain", "/api/discuss", "/api/outcome"):
            return self._coach(path.rsplit("/", 1)[1], data)
        if path.startswith("/api/memory"):
            return self._memory(path, data)
        m = re.match(r"^/api/telegram/([^/]+)$", path)
        if m and ID_RE.match(m.group(1)):
            return self._send_telegram(m.group(1))
        self._json(404, {"error": "not found"})

    def _review(self, data: dict):
        try:
            chart = decode_image(data.get("image") or "")
        except Exception as e:
            return self._json(400, {"error": f"无法读取图片：{e}"})
        note = (data.get("note") or "").strip()[:500]
        out = AGENT.handle({"mode": "review", "image": chart, "note": note, "text": note,
                            "session_id": self._coach_session(), "source": "web"})
        if not out["ok"]:
            return self._json(500, {"error": out["error"]})
        self._json(200, {"ok": True, "meta": out["meta"], "text": out["text"], "case_id": out["case_id"]})

    def _coach(self, kind: str, data: dict):
        mode = {"study": "study", "decision": "decision", "explain": "explain", "discuss": "discuss",
                "outcome": "outcome"}[kind]
        if mode == "discuss" and data.get("annotation_id"):
            mode = "explain"
        req = {"mode": mode, "text": (data.get("text") or "").strip()[:4000], "session_id": self._coach_session(),
               "source": "web", "depth": data.get("depth") or "detailed"}
        if data.get("case_id"):
            if not ID_RE.match(data["case_id"]):
                return self._json(400, {"error": "bad case id"})
            req["case_id"] = data["case_id"]
        if data.get("annotation_id"):
            req["annotation_id"] = str(data["annotation_id"])[:40]
        if mode == "decision":
            if data.get("image"):
                try:
                    req["image"] = decode_image(data["image"])
                except Exception as e:
                    return self._json(400, {"error": f"无法读取图片：{e}"})
            if (data.get("csv") or "").strip():
                req["csv"] = data["csv"].strip()
            if (data.get("as_of") or "").strip():
                req["as_of"] = data["as_of"].strip()
        out = AGENT.handle(req)
        if not out["ok"]:
            return self._json(500, {"error": out["error"]})
        if out.get("figures"):
            out["figures"] = [{"evidence_id": f["evidence_id"], "name": os.path.basename(f["path"])} for f in out["figures"]]
        if out.get("revision"):
            out["revision"]["image_name"] = os.path.basename(out["revision"]["image"])
        out.pop("spec", None)
        self._json(200, out)

    def _memory(self, path: str, data: dict):
        mem = AGENT.memory
        try:
            if m := re.match(r"^/api/memory/candidate/(\d+)/(approve|reject|delete)$", path):
                cid, act = int(m.group(1)), m.group(2)
                if act == "approve":
                    mem.approve_candidate(cid, approved_by="web-user", note="approved in web UI")
                elif act == "reject":
                    mem.reject_candidate(cid, "rejected in web UI")
                else:
                    mem.delete_candidate(cid)
            elif path == "/api/memory/profile":
                mem.add_profile(data.get("category", "learning_difficulty"), data["content"][:1000], confirmed_by="web-user")
            elif m := re.match(r"^/api/memory/profile/(\d+)/(delete|update)$", path):
                if m.group(2) == "delete":
                    mem.delete_profile(int(m.group(1)))
                else:
                    mem.update_profile(int(m.group(1)), data["content"][:1000])
            elif path == "/api/memory/rule":
                mem.add_rule(data["rule"][:1000], approved_by="web-user")
            elif m := re.match(r"^/api/memory/rule/(\d+)/delete$", path):
                mem.delete_rule(int(m.group(1)))
            else:
                return self._json(404, {"error": "not found"})
        except (KeyError, ValueError, PermissionError) as e:
            return self._json(400, {"error": str(e)})
        self._json(200, {"ok": True})

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
            Bot(CFG, Telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]), agent=AGENT).run()
        except Exception:
            log.exception("Telegram bot stopped")
    threading.Thread(target=run, daemon=True, name="telegram").start()


def main():
    global CFG, TG, AGENT
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
    from agent.runtime import CoachAgent
    AGENT = CoachAgent(CFG, project_dir=".")
    log.info("Brooks knowledge: %s", AGENT.kb.stats() if AGENT.kb else "UNAVAILABLE (answers will lack citations)")

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
