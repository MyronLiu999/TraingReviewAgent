"""Mode router: decides which workflow handles a request.

Rules, in order (an explicit `mode` always wins):
  outcome   - explicit only ("what happened afterwards" for a saved case)
  memory    - the user asks the coach to remember something ("以后请记住…", "remember that…")
  explain   - the text points at a mark (#3, H2_001, R2, Z1) and a case is in focus or given
  discuss   - any other follow-up / challenge while a case is in focus (no new chart attached)
  decision  - an image/OHLC is attached and the user asks what to do (long/short/entry/现在…)
  review    - an image is attached otherwise
  study     - everything else (theory questions, follow-ups)
"""

import re

MODES = ("study", "review", "decision", "explain", "discuss", "memory", "outcome")
# Only forward-looking questions trigger decision mode; "我在高点做空了" describes a past trade -> review.
DECISION_RE = re.compile(r"(现在|当前|目前|接下来|下一步|此刻).{0,12}(多|空|做|关注|入场|进场|交易)|"
                         r"做多还是做空|做空还是做多|多还是空|空还是多|偏多还是偏空|该不该|要不要(做|进|入|买|卖)|"
                         r"应该.{0,6}(做多|做空|关注|入场|进场)|交易计划|^/(decide|trade)|should i|long or short|\bbias\b|"
                         r"(在)?哪(里|儿)?.{0,2}(入场|进场|离场|出场|止损|止盈|买|卖)|能不能(做|进|入|买|卖)|"
                         r"可以(做|进场|入场|买|卖|做多|做空)吗|建议.{0,6}(做|入场|进场|交易)|怎么(做|交易|操作)|"
                         r"(入场|进场)点|止盈|where (to|should i) (enter|exit|buy|sell)|\btrade plan\b", re.I)
MEMORY_RE = re.compile(r"(以后|今后|从现在起|从今往后).{0,8}(记住|记得|都要|一律)|^\s*(请)?记住|记下来|"
                       r"\bremember (that|this)\b|\balways remember\b", re.I)
ANN_RE = re.compile(r"#(\d{1,2})(?!\d)|\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d{3})\b|\b([RZ]\d{1,2})\b")


def find_annotation_ref(text: str) -> str | None:
    """'#3' -> '#3'; 'H2_001' -> 'H2_001'; 'R2' -> 'R2'."""
    m = ANN_RE.search(text or "")
    if not m:
        return None
    return f"#{m.group(1)}" if m.group(1) else (m.group(2) or m.group(3))


def route(request: dict, session_state: dict | None = None) -> str:
    explicit = (request.get("mode") or "").lower()
    text = request.get("text") or ""
    # "remember X" typed into a chat box (the web tabs send study/discuss) must still become a
    # candidate with the approval notice, not a chat answer that says "got it"
    if explicit in ("study", "discuss", "explain") and MEMORY_RE.search(text):
        return "memory"
    if explicit in MODES:
        return explicit
    has_chart = bool(request.get("image") is not None or request.get("image_path") or request.get("csv"))
    if MEMORY_RE.search(text):
        return "memory"
    in_focus = request.get("case_id") or (session_state or {}).get("current_case_id")
    if not has_chart and in_focus and (request.get("annotation_id") or find_annotation_ref(text)):
        return "explain"
    if not has_chart and in_focus and text.strip():
        return "discuss"
    if has_chart:
        return "decision" if DECISION_RE.search(text) else "review"
    return "study"
