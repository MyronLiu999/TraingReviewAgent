"""Chart review: send a chart screenshot to an OpenAI vision model, get back a
structured list of price-action annotations, and draw them onto the chart.

Usage (without Telegram):
    python chart_review.py my_chart.png               # writes reviews/<name>_review.png
    python chart_review.py my_chart.png --note "MYM 5分钟, 我在高点做空了"
"""

import argparse
import base64
import glob
import io
import json
import logging
import math
import os
import re
from datetime import datetime

import openai
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

GRID = 1000  # the model gives every position on a 0-1000 scale (x: left→right, y: top→bottom)

KNOWLEDGE_PRIMER = """\
Use Al Brooks price action concepts and terminology:
- Always In Long / Always In Short (AIL / AIS): which side is in control. A strong breakout
  with follow-through flips the always-in direction.
- Trend vs. trading range (TR). Tight trading range (TTR / barbwire): overlapping bars,
  many dojis (a doji is a 1-bar trading range). Most breakouts of a TR fail.
- Breakout (BO) and follow-through; spike and channel; measured move (MM).
- Climaxes: buy climax / sell climax (several big trend bars late in a move), often followed
  by a TR or reversal. Consecutive climaxes, exhaustion.
- Wedge (3 pushes), wedge top / bottom; parabolic wedge.
- Major Trend Reversal (MTR): trend-line break, then a test of the extreme (higher low HL /
  lower high LH / double bottom / double top), then a strong reversal.
- Double top / double bottom (DT / DB), head and shoulders (HST / HSB), double bottom bull
  flag, double top bear flag, final flag.
- High 1 / High 2 (H1 / H2) buys, Low 1 / Low 2 (L1 / L2) sells, second entries.
- Pullbacks to the 20-bar EMA in a trend are with-trend entries.
- Signal bar and entry bar; gaps between bars in a strong breakout show urgency.
"""

SYSTEM_PROMPT = """\
You are an expert price-action trading coach. The user sends a screenshot of a candlestick
chart. You produce a post-trade review ("复盘") by placing annotations on the chart, in the
style of an Al Brooks annotated chart: shaded zones for market phases, rings on key bars,
and short labels with arrows pointing at the exact bars they describe.

# KNOWLEDGE BASE
You must analyze the chart with the knowledge base below. It contains a decision framework
(DF-1 ... DF-7) and rules with IDs (e.g. PA-H-002, PA-MTR-001, TD-005). Work through the
decision framework IN ORDER before deciding on any annotation, and only apply a rule when its
"Required Context" is actually met on this chart. Check each rule's "Invalidation" and
"Failure Scenarios" before you use it.

{knowledge}

# COORDINATES
The image you see has a faint yellow coordinate grid drawn on it, labeled 0-1000 on both
axes (x from left to right, y from top to bottom). Give every position in these grid units.
Read positions carefully from the grid labels. Precise placement matters more than quantity.

# WHAT TO ANNOTATE
1. Split the chart into phases with `zones` (trading_range, always_in_long, always_in_short,
   pattern). Zones usually span the full chart height for always-in phases, and tightly
   wrap the price action for trading ranges and patterns.
2. Put `rings` on the key bars: climaxes, reversal points, tops/bottoms of double tops,
   higher lows / lower highs, signal bars.
3. Add 6-12 `callouts`. Each callout has a short label and an arrow to the bar it describes.
   Set `rule_id` to the knowledge-base rule that justifies the callout (e.g. "PA-H-002"),
   or "" if no rule applies.
   Format the text as "English term 中文解释", at most 2 short lines (use \\n between lines).
   Examples: "Sell Climax 卖出高潮", "Bull Breakout + Follow-through\\n多头突破 + 跟进",
   "H2 buy 第二次买点", "Double Top 双顶 (DT)".
4. Write a `summary` (Chinese, 4-8 short lines, each starting with ①②③... or "当前："):
   the story of the chart, then the current situation and what would confirm the bull or
   bear case next.
5. Fill `rules_applied`: every rule you relied on or explicitly rejected, with what you saw on
   the chart (`observation`, Chinese, one sentence) and `status`:
   met = context and detection logic satisfied; partial = some conditions missing (say which);
   rejected = looks like the pattern but the rule's invalidation / failure case applies.
   Include the TD-* rules you used to judge trades (stops, targets, when not to trade).
6. Write `analysis` (Chinese, max ~900 characters) following the decision framework, with
   these headings on their own lines:
   【市场背景】 phases and Always In direction (DF-2)
   【关键结构】 the key events (DF-3)
   【交易机会】 the good setups: entry, stop, target, trader's equation (DF-4)
   【应避免的交易】 traps and trades not to take (DF-5); if the user described their own
   trade, judge it point by point here
   【当前与下一步】 current state and what would confirm bulls or bears (DF-6)
   Cite rule IDs in square brackets, e.g. [PA-H-002]. Never invent win rates.

# LAYOUT RULES
- Label positions (label_x, label_y = top-left corner of the label) must be in EMPTY space:
  never on top of candles, the chart title, the price axis, or the trading buttons/orders.
- Arrows must end exactly on the bar or area being described (target_x, target_y).
- Labels must not overlap each other. Keep labels short.
- summary_x / summary_y: top-left corner of an empty area (roughly 200 x 200 grid units)
  for the summary box.
- Colors: red = bearish, green = bullish, blue = trading range, magenta = patterns and
  setups, orange = entries / signal bars, white = neutral.

# HONESTY
If the image is not a price chart, say so in analysis and return empty lists.
If part of the chart is ambiguous, say so instead of inventing structure.
This is education and review, not financial advice.
"""

REFINE_PROMPT = """\
Here is the chart with your annotations drawn on it (grid still visible), and the JSON you
returned. Check every element carefully:
- Does each arrow end exactly on the bar it describes? Is each ring centered on the right bar?
- Is any label covering candles, the title, the price axis, other labels, or order lines?
- Do the zones match the real phases of the price action?
- Is the price-action reading itself correct? Was each rule applied only where its Required
  Context is met, and does each callout's rule_id match what it describes?
Return the full corrected JSON (same schema). Keep what is right; fix what is wrong.
Previous JSON:
"""

COLOR_ENUM = ["red", "green", "blue", "magenta", "orange", "white"]
NUM = {"type": "number"}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "chart_description": {"type": "string"},
        "zones": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["always_in_long", "always_in_short",
                                                    "trading_range", "pattern"]},
                "x1": NUM, "y1": NUM, "x2": NUM, "y2": NUM,
                "title": {"type": "string"},
            },
            "required": ["kind", "x1", "y1", "x2", "y2", "title"],
            "additionalProperties": False,
        }},
        "rings": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "x": NUM, "y": NUM,
                "size": {"type": "string", "enum": ["small", "medium", "large"]},
                "color": {"type": "string", "enum": COLOR_ENUM},
            },
            "required": ["x", "y", "size", "color"],
            "additionalProperties": False,
        }},
        "callouts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "color": {"type": "string", "enum": COLOR_ENUM},
                "label_x": NUM, "label_y": NUM,
                "target_x": NUM, "target_y": NUM,
                "arrow": {"type": "boolean"},
                "rule_id": {"type": "string"},
            },
            "required": ["text", "color", "label_x", "label_y", "target_x", "target_y", "arrow",
                         "rule_id"],
            "additionalProperties": False,
        }},
        "rules_applied": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "concept": {"type": "string"},
                "status": {"type": "string", "enum": ["met", "partial", "rejected"]},
                "observation": {"type": "string"},
            },
            "required": ["rule_id", "concept", "status", "observation"],
            "additionalProperties": False,
        }},
        "summary": {"type": "string"},
        "summary_x": NUM, "summary_y": NUM,
        "analysis": {"type": "string"},
    },
    "required": ["chart_description", "zones", "rings", "callouts", "rules_applied", "summary",
                 "summary_x", "summary_y", "analysis"],
    "additionalProperties": False,
}

RGB = {"red": (255, 90, 90), "green": (80, 230, 120), "blue": (90, 170, 255),
       "magenta": (255, 80, 220), "orange": (255, 165, 40), "white": (235, 235, 235)}
ZONE_STYLE = {  # fill RGBA, outline RGBA (None = no outline), title color
    "always_in_long": ((60, 220, 110, 28), None, "green"),
    "always_in_short": ((255, 60, 60, 40), None, "red"),
    "trading_range": ((90, 150, 255, 50), (90, 170, 255, 190), "blue"),
    "pattern": ((255, 210, 60, 30), (255, 200, 60, 170), "orange"),
}

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",                      # Windows: Microsoft YaHei
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",     # macOS
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",   # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_font() -> str | None:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    log.warning("No Chinese font found; Chinese text may not display. Install a CJK font.")
    return None


def load_knowledge_base(folder: str, limit: int = 150_000) -> str:
    """Reads the structured knowledge base (.md files). The master index and the decision
    framework go first so they are never cut off when the base is larger than `limit`."""
    files = sorted(glob.glob(os.path.join(folder, "**", "*.md"), recursive=True))
    first = [f for f in files if re.match(r"0[07]_", os.path.basename(f))]
    texts = []
    for path in first + [f for f in files if f not in first]:
        try:
            texts.append(f"## {os.path.relpath(path, folder)}\n" + open(path, encoding="utf-8").read())
        except OSError as e:
            log.warning("Could not read %s: %s", path, e)
    joined = "\n\n".join(texts)
    if len(joined) > limit:
        log.warning("Knowledge base is long (%d chars); using the first %d", len(joined), limit)
        joined = joined[:limit]
    return joined


def load_knowledge(folder: str = "docs", limit: int = 120_000) -> str:
    """Reads the user's study material from docs/ (.md, .txt, and .pdf if pypdf is installed)."""
    texts = []
    for path in sorted(glob.glob(os.path.join(folder, "**", "*"), recursive=True)):
        ext = os.path.splitext(path)[1].lower()
        if os.path.basename(path).lower() == "readme.md":
            continue  # instructions for the user, not study material
        try:
            if ext in (".md", ".txt"):
                texts.append(f"## {os.path.basename(path)}\n" + open(path, encoding="utf-8").read())
            elif ext == ".pdf":
                from pypdf import PdfReader
                pages = [p.extract_text() or "" for p in PdfReader(path).pages]
                texts.append(f"## {os.path.basename(path)}\n" + "\n".join(pages))
        except Exception as e:
            log.warning("Could not read %s: %s", path, e)
    joined = "\n\n".join(texts)
    if len(joined) > limit:
        log.warning("docs/ is long (%d chars); using the first %d", len(joined), limit)
        joined = joined[:limit]
    return joined


# ---------------------------------------------------------------- image helpers

def with_grid(img: Image.Image) -> Image.Image:
    """Copy of the chart with a faint 0-1000 coordinate grid, so the model can read positions."""
    out = img.convert("RGBA")
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = out.size
    font = ImageFont.truetype(find_font(), max(12, w // 110)) if find_font() else None
    for v in range(0, GRID + 1, 50):
        major = v % 100 == 0
        alpha = 110 if major else 45
        x, y = v * w / GRID, v * h / GRID
        d.line([(x, 0), (x, h)], fill=(255, 255, 0, alpha), width=1)
        d.line([(0, y), (w, y)], fill=(255, 255, 0, alpha), width=1)
        if major:
            d.text((x + 2, h - 18), str(v), fill=(255, 255, 0, 230), font=font)
            d.text((2, y + 2), str(v), fill=(255, 255, 0, 230), font=font)
    return Image.alpha_composite(out, layer).convert("RGB")


def to_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def prepare(img: Image.Image, max_side: int = 2000) -> Image.Image:
    img = img.convert("RGB")
    scale = max_side / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return img


# ---------------------------------------------------------------- drawing

def render(chart: Image.Image, spec: dict) -> Image.Image:
    w, h = chart.size
    px = lambda v: v * w / GRID
    py = lambda v: v * h / GRID
    unit = w / 2000  # scale line widths and fonts with image size
    font_path = find_font()
    font = lambda s: (ImageFont.truetype(font_path, max(10, round(s * unit)))
                      if font_path else ImageFont.load_default())

    img = chart.convert("RGBA")
    zones = Image.new("RGBA", img.size, (0, 0, 0, 0))
    zd = ImageDraw.Draw(zones)
    for zn in spec.get("zones", []):
        fill, outline, _ = ZONE_STYLE.get(zn["kind"], ZONE_STYLE["pattern"])
        box = (px(min(zn["x1"], zn["x2"])), py(min(zn["y1"], zn["y2"])),
               px(max(zn["x1"], zn["x2"])), py(max(zn["y1"], zn["y2"])))
        zd.rectangle(box, fill=fill, outline=outline, width=max(1, round(2 * unit)))
    img = Image.alpha_composite(img, zones)
    d = ImageDraw.Draw(img)
    placed: list[tuple] = []

    def overlaps(a, b):
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])

    def label(x, y, text, color, size=19, bg=(10, 12, 20, 210)):
        """Draws a boxed label with its top-left near (x, y), moved to the nearest free spot
        if it would overlap an earlier label. Returns the box."""
        f = font(size)
        pad = 6 * unit
        tb = d.multiline_textbbox((0, 0), text, font=f, spacing=4)
        bw, bh = tb[2] - tb[0], tb[3] - tb[1]

        def box_at(cx, cy):
            cx = min(max(cx, pad), w - bw - pad)  # keep inside the image
            cy = min(max(cy, pad), h - bh - pad)
            return (cx - pad, cy - pad, cx + bw + pad, cy + bh + pad)

        step_y, step_x = bh + 2 * pad + 4 * unit, bw / 2
        candidates = [(0, 0)]
        for k in range(1, 7):
            candidates += [(0, k * step_y), (0, -k * step_y),
                           (k * step_x, 0), (-k * step_x, 0)]
        box = box_at(x, y)
        for dx, dy in candidates:
            cand = box_at(x + dx, y + dy)
            if not any(overlaps(cand, p) for p in placed):
                box = cand
                break
        placed.append(box)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(box, radius=6 * unit, fill=bg,
                                                outline=RGB[color] + (255,), width=max(1, round(2 * unit)))
        img.alpha_composite(layer)
        # offset by the text's own bbox origin so the text sits exactly inside the box
        d.multiline_text((box[0] + pad - tb[0], box[1] + pad - tb[1]), text, font=f,
                         fill=RGB[color], spacing=4)
        return box

    for zn in spec.get("zones", []):
        if zn.get("title"):
            color = ZONE_STYLE.get(zn["kind"], ZONE_STYLE["pattern"])[2]
            label(px(min(zn["x1"], zn["x2"])) + 8 * unit, py(min(zn["y1"], zn["y2"])) + 8 * unit,
                  zn["title"], color, 20)

    ring_r = {"small": 12, "medium": 18, "large": 26}
    for r in spec.get("rings", []):
        rad = ring_r.get(r["size"], 18) * unit
        cx, cy = px(r["x"]), py(r["y"])
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), outline=RGB[r["color"]],
                  width=max(2, round(3 * unit)))

    for c in spec.get("callouts", []):
        text = c["text"] + (f"\n[{c['rule_id']}]" if c.get("rule_id") else "")
        box = label(px(c["label_x"]), py(c["label_y"]), text, c["color"], 18)
        if c.get("arrow", True):
            tx, ty = px(c["target_x"]), py(c["target_y"])
            # start the arrow at the label edge closest to the target
            sx = min(max(tx, box[0]), box[2])
            sy = min(max(ty, box[1]), box[3])
            if (sx, sy) != (tx, ty):
                width = max(2, round(3 * unit))
                d.line([(sx, sy), (tx, ty)], fill=RGB[c["color"]], width=width)
                ang = math.atan2(ty - sy, tx - sx)
                head = 14 * unit
                for s in (-0.45, 0.45):
                    d.line([(tx, ty), (tx - head * math.cos(ang + s), ty - head * math.sin(ang + s))],
                           fill=RGB[c["color"]], width=width)

    if spec.get("summary"):
        label(px(spec.get("summary_x", 790)), py(spec.get("summary_y", 140)),
              "复盘总结 Review\n" + spec["summary"], "white", 16, bg=(10, 12, 20, 230))
    return img.convert("RGB")


# ---------------------------------------------------------------- model calls

class ChartReviewer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = openai.OpenAI()  # reads OPENAI_API_KEY from the environment
        kb = load_knowledge_base(cfg.get("knowledge_base", "knowledge"))
        if kb:
            knowledge = kb
            log.info("Loaded %d characters of knowledge base", len(kb))
        else:
            log.warning("Knowledge base not found at %s; using the short built-in primer",
                        cfg.get("knowledge_base", "knowledge"))
            knowledge = KNOWLEDGE_PRIMER
        docs = load_knowledge(cfg.get("docs_folder", "docs"))
        if docs:
            knowledge += ("\n# THE USER'S OWN STUDY MATERIAL (follow its terms and rules first)\n"
                          + docs)
            log.info("Loaded %d characters of study material from docs/", len(docs))
        # replace() rather than format(): study material may contain { } characters
        self.system = SYSTEM_PROMPT.replace("{knowledge}", knowledge)

    def _ask(self, content: list) -> dict:
        response = self.client.chat.completions.create(
            model=self.cfg.get("review_model", "gpt-4o"),
            max_completion_tokens=6000,
            messages=[{"role": "system", "content": self.system},
                      {"role": "user", "content": content}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "chart_review", "strict": True, "schema": OUTPUT_SCHEMA}},
        )
        choice = response.choices[0]
        if choice.message.refusal:
            raise RuntimeError(f"Model declined: {choice.message.refusal}")
        if choice.finish_reason == "length" or not choice.message.content:
            raise RuntimeError("Model response was cut off or empty")
        return json.loads(choice.message.content)

    def review(self, chart: Image.Image, note: str = "") -> tuple[Image.Image, dict]:
        """Returns (annotated image, spec). `note` is optional context from the user."""
        chart = prepare(chart)
        intro = "Review this chart and return the annotations."
        if note:
            intro += f"\nUser's note about this chart / their trade: {note}"
        spec = self._ask([
            {"type": "text", "text": intro},
            {"type": "image_url", "image_url": {"url": to_data_url(with_grid(chart)), "detail": "high"}},
        ])
        for _ in range(int(self.cfg.get("review_refine_passes", 1))):
            draft = with_grid(render(chart, spec))
            spec = self._ask([
                {"type": "text", "text": REFINE_PROMPT + json.dumps(spec, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": to_data_url(draft), "detail": "high"}},
            ])
        return render(chart, spec), spec


REVIEWS_DIR = "reviews"


def store_review(original: Image.Image, img: Image.Image, spec: dict | None,
                 note: str = "", source: str = "web") -> dict:
    """Saves one review as reviews/<id>/{original.png, review.png, meta.json}; returns meta."""
    rid = f"{datetime.now():%Y%m%d_%H%M%S}_{os.urandom(2).hex()}"
    folder = os.path.join(REVIEWS_DIR, rid)
    os.makedirs(folder)
    prepare(original).save(os.path.join(folder, "original.png"))
    img.save(os.path.join(folder, "review.png"))
    spec = spec or {}
    meta = {
        "id": rid,
        "created": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "note": note,
        "description": spec.get("chart_description", ""),
        "summary": spec.get("summary", ""),
        "analysis": spec.get("analysis", ""),
        "rules_applied": spec.get("rules_applied", []),
        "spec": spec,
    }
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def list_reviews(limit: int = 200) -> list[dict]:
    """Newest first. Each item is the review's meta without the bulky spec."""
    out = []
    for meta_path in sorted(glob.glob(os.path.join(REVIEWS_DIR, "*", "meta.json")), reverse=True)[:limit]:
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta.pop("spec", None)
        out.append(meta)
    return out


def main() -> None:
    import tomllib
    from main import load_env
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--note", default="", help="optional context, e.g. instrument or your trade")
    ap.add_argument("--spec", help="draw from a saved JSON spec instead of calling the model")
    args = ap.parse_args()
    with open("config.toml", "rb") as f:
        cfg = tomllib.load(f)

    chart = Image.open(args.image)
    if args.spec:
        spec = json.load(open(args.spec, encoding="utf-8"))
        img = render(prepare(chart), spec)
    else:
        img, spec = ChartReviewer(cfg).review(chart, args.note)
    meta = store_review(chart, img, spec, args.note, source="cli")
    print(meta["analysis"])
    print(f"\nSaved: {os.path.join(REVIEWS_DIR, meta['id'], 'review.png')}")


if __name__ == "__main__":
    main()
