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
   Format the text as "English term 中文解释", at most 2 short lines (use \\n between lines).
   Examples: "Sell Climax 卖出高潮", "Bull Breakout + Follow-through\\n多头突破 + 跟进",
   "H2 buy 第二次买点", "Double Top 双顶 (DT)".
4. Write a `summary` (Chinese, 4-8 short lines, each starting with ①②③... or "当前："):
   the story of the chart, then the current situation and what would confirm the bull or
   bear case next.
5. Write `analysis`: a slightly fuller Chinese explanation for the chat message (max ~600
   Chinese characters), including what a good trade would have been and common mistakes.

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
- Is the price-action reading itself correct?
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
            },
            "required": ["text", "color", "label_x", "label_y", "target_x", "target_y", "arrow"],
            "additionalProperties": False,
        }},
        "summary": {"type": "string"},
        "summary_x": NUM, "summary_y": NUM,
        "analysis": {"type": "string"},
    },
    "required": ["chart_description", "zones", "rings", "callouts", "summary",
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
        f = font(size)
        pad = 6 * unit
        box = d.multiline_textbbox((x, y), text, font=f, spacing=4)
        bw, bh = box[2] - box[0], box[3] - box[1]
        x = min(max(x, pad), w - bw - pad)  # keep inside the image
        y = min(max(y, pad), h - bh - pad)
        for _ in range(12):  # nudge down/up to avoid overlapping earlier labels
            box = (x - pad, y - pad, x + bw + pad, y + bh + pad)
            if not any(overlaps(box, p) for p in placed):
                break
            y = y + bh + 2 * pad if y + 2 * bh < h else y - bh - 2 * pad
        placed.append(box)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(box, radius=6 * unit, fill=bg,
                                                outline=RGB[color] + (255,), width=max(1, round(2 * unit)))
        img.alpha_composite(layer)
        d.multiline_text((x, y), text, font=f, fill=RGB[color], spacing=4)
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
        box = label(px(c["label_x"]), py(c["label_y"]), c["text"], c["color"], 18)
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
        knowledge = KNOWLEDGE_PRIMER
        docs = load_knowledge(cfg.get("docs_folder", "docs"))
        if docs:
            knowledge += ("\n# THE USER'S OWN STUDY MATERIAL (follow its terms and rules first)\n"
                          + docs)
            log.info("Loaded %d characters of study material from docs/", len(docs))
        self.system = SYSTEM_PROMPT.format(knowledge=knowledge)

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


def save_review(img: Image.Image, name: str = "chart", folder: str = "reviews") -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{datetime.now():%Y-%m-%d_%H%M%S}_{name}_review.png")
    img.save(path)
    return path


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
        img = render(prepare(chart), json.load(open(args.spec, encoding="utf-8")))
        spec = None
    else:
        img, spec = ChartReviewer(cfg).review(chart, args.note)
    name = os.path.splitext(os.path.basename(args.image))[0]
    path = save_review(img, name)
    if spec:
        json.dump(spec, open(path.replace(".png", ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(spec["analysis"])
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
