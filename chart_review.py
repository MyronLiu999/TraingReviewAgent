"""Chart review: send a chart screenshot to an OpenAI vision model, get back a
structured list of price-action annotations, and draw them onto the chart.

Every drawn element gets a stable id in an annotation registry (spec["annotations"]):
callouts are numbered #1, #2, ... on the image and carry an id such as "H2_001", so you can
later ask "why is #3 an H2?" and the coach finds that exact mark, its bar and its reasoning.

Usage (without Telegram):
    python chart_review.py my_chart.png               # writes reviews/<id>/review.png
    python chart_review.py my_chart.png --note "MYM 5分钟, 我在高点做空了"
    python chart_review.py my_chart.png --spec saved.json   # redraw a saved spec, no model call
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
Read positions carefully from the grid labels: a mark on a bar's high must use the y of that
high, a mark on a low the y of that low. Precise placement matters more than quantity.

# WHAT TO ANNOTATE
0. First decide the context: `market_context` (trend / broad channel / trading range / tight
   range, which phase of the cycle) and `always_in` (long / short / unclear) for the RIGHT EDGE.
   `current_state` describes the last 5-15 bars at the right edge: they matter most.
1. Split the chart into phases with `zones` (trading_range, always_in_long, always_in_short,
   pattern). A phase zone starts and ends where that phase starts and ends - do not let a
   bear-trend zone cover the rally after the low. Trading-range zones tightly wrap the bars.
2. Put `rings` on the key bars: climaxes, reversal points, tops/bottoms of double tops,
   higher lows / lower highs, signal bars.
3. Add 6-12 `callouts`. Each callout has a short label and an arrow to the bar it describes.
   Format the text as "English term 中文解释", at most 2 short lines (use \\n between lines).
   - `type`: the setup or structure (H2, L2, signal_bar, breakout, sell_climax, ...).
   - `status`: "confirmed" only if the bars clearly show it; "candidate" if it is your best
     reading; "uncertain" if the screenshot does not let you check it (blurry, cut off, bars too
     small). Never present an uncertain structure as confirmed.
   - `bar_ref`: which bar(s) you mean, as the user would find them (time / price / "the third
     bear bar after the 09:00 spike"). Use prices or times only if you can read them.
   - `reason`: one or two sentences: why this label, which bars support it, why not the
     look-alike (e.g. why H2 and not H1, why a breakout and not a climax).
   A spike that STARTS a trend is a breakout, not a climax; a climax comes late in a move.
   Count H1/H2 (L1/L2) only inside a pullback, following the counting rules above.
4. Write `summary_lines` (Chinese, 4-8 short lines, each starting with ①②③... or "当前："):
   the story of the chart, then the current situation and what would confirm the bull or
   bear case next.
5. Write `analysis`: a slightly fuller Chinese explanation for the chat message (max ~600
   Chinese characters), including what a good trade would have been and common mistakes.
   Use only probabilities that appear in the knowledge above, with their conditions.
6. `limitations`: what you could not read reliably (prices, small bars, cut-off areas).

# LAYOUT RULES
- Label positions (label_x, label_y = top-left corner of the label) must be in EMPTY space:
  never on top of candles, the chart title, the price axis, or the trading buttons/orders.
- Arrows must end exactly on the bar or area being described (target_x, target_y).
- Labels must not overlap each other. Keep labels short.
- summary_x / summary_y: top-left corner of an empty area (roughly 300 x 200 grid units)
  for the summary box.
- Colors: red = bearish, green = bullish, blue = trading range, magenta = patterns and
  setups, orange = entries / signal bars, white = neutral.

# HONESTY
If the image is not a price chart, say so in analysis and return empty lists.
If part of the chart is ambiguous, say so instead of inventing structure.
Only judge with what the screenshot shows: never assume what happened after its last bar.
This is education and review, not financial advice.
"""

REFINE_PROMPT = """\
Here is the chart with your annotations drawn on it (grid still visible), and the JSON you
returned. Check every element carefully:
- Does each arrow end exactly on the bar it describes? Is each ring centered on the right bar?
- Is any label covering candles, the title, the price axis, other labels, or order lines?
- Do the zones match the real phases of the price action (start and end on the right bars)?
- Is the price-action reading itself correct (context, always-in direction, right edge)?
- Is every status honest (uncertain where the screenshot cannot show it)?
Return the full corrected JSON (same schema). Keep what is right; fix what is wrong.
Previous JSON:
"""

COLOR_ENUM = ["red", "green", "blue", "magenta", "orange", "white"]
ANNOTATION_TYPES = ["H1", "H2", "H3", "H4", "L1", "L2", "L3", "L4", "signal_bar", "entry_bar", "breakout",
                    "failed_breakout", "breakout_pullback", "follow_through", "buy_climax", "sell_climax",
                    "double_top", "double_bottom", "higher_low", "lower_high", "higher_high", "lower_low",
                    "wedge", "mtr", "final_flag", "trend_line_break", "trading_range", "tight_trading_range",
                    "micro_channel", "measured_move", "gap", "ema_test", "reversal_bar", "other"]
STATUS_ENUM = ["confirmed", "candidate", "uncertain"]
# Which end of the bars an annotation type points at (used to snap arrows onto candles)
TOP_TYPES = {"buy_climax", "double_top", "lower_high", "higher_high", "L1", "L2", "L3", "L4"}
BOTTOM_TYPES = {"sell_climax", "double_bottom", "higher_low", "lower_low", "H1", "H2", "H3", "H4"}
NUM = {"type": "number"}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "chart_description": {"type": "string"},
        "market_context": {"type": "string"},
        "always_in": {"type": "string", "enum": ["long", "short", "unclear"]},
        "current_state": {"type": "string"},
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
                "type": {"type": "string", "enum": ANNOTATION_TYPES},
                "status": {"type": "string", "enum": STATUS_ENUM},
                "bar_ref": {"type": "string"},
                "reason": {"type": "string"},
                "color": {"type": "string", "enum": COLOR_ENUM},
                "label_x": NUM, "label_y": NUM,
                "target_x": NUM, "target_y": NUM,
                "arrow": {"type": "boolean"},
            },
            "required": ["text", "type", "status", "bar_ref", "reason", "color", "label_x", "label_y",
                         "target_x", "target_y", "arrow"],
            "additionalProperties": False,
        }},
        "summary_lines": {"type": "array", "items": {"type": "string"}},
        "summary_x": NUM, "summary_y": NUM,
        "analysis": {"type": "string"},
        "limitations": {"type": "string"},
    },
    "required": ["chart_description", "market_context", "always_in", "current_state", "zones", "rings",
                 "callouts", "summary_lines", "summary_x", "summary_y", "analysis", "limitations"],
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


def load_primer(cfg: dict) -> str | None:
    """Reads the Al Brooks review primer. Prefers the hash-verified copy in the active canonical
    version (versions/<v>/derived/), then the knowledge base folder, if configured."""
    store, name = cfg.get("canonical_store", ""), cfg.get("knowledge_primer", "11_REVIEW_AGENT_PRIMER.md")
    if store and os.path.isfile(os.path.join(store, "ACTIVE")):
        from knowledge.manifest import active_version, load_manifest, sha256_file, version_dir
        version = active_version(store)
        path = os.path.join(version_dir(store, version), "derived", name)
        art = next((a for a in load_manifest(store, version)["artifacts"] if a["path"] == f"derived/{name}"), None)
        if art and os.path.isfile(path) and sha256_file(path) == art["sha256"]:
            with open(path, encoding="utf-8") as f:
                return f.read()
        log.warning("Primer in canonical version %s missing or modified; falling back", version)
    folder = cfg.get("knowledge_base_folder", "")
    path = os.path.join(folder, cfg.get("knowledge_primer", "11_REVIEW_AGENT_PRIMER.md"))
    if not folder or not os.path.isfile(path):
        if folder:
            log.warning("Knowledge primer not found at %s; using the built-in primer", path)
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_knowledge(folder: str = "docs", limit: int = 120_000, pdfs: bool = True) -> str:
    """Reads the user's study material from docs/ (.md, .txt, and .pdf if pypdf is installed)."""
    texts = []
    for path in sorted(glob.glob(os.path.join(folder, "**", "*"), recursive=True)):
        ext = os.path.splitext(path)[1].lower()
        if os.path.basename(path).lower() == "readme.md":
            continue  # instructions for the user, not study material
        if ext == ".pdf" and not pdfs:
            continue
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
            # repeat the labels inside the chart so positions near the bars are easy to read
            for col in (300, 600, 850):
                d.text((col * w / GRID + 2, y + 2), str(v), fill=(255, 255, 0, 150), font=font)
            d.text((x + 2, 500 * h / GRID + 2), str(v), fill=(255, 255, 0, 150), font=font)
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


def grid_to_px(x: float, y: float, size: tuple[int, int]) -> tuple[float, float]:
    """Grid units (0-1000) -> pixels of an image of `size`. Works for any rescaled copy of the
    chart, because the grid is relative to the image, not to its pixel count."""
    return x * size[0] / GRID, y * size[1] / GRID


def px_to_grid(px: float, py: float, size: tuple[int, int]) -> tuple[float, float]:
    return px * GRID / size[0], py * GRID / size[1]


def crop_around(img: Image.Image, x: float, y: float, half: float = 110) -> Image.Image:
    """Zoomed crop around a grid point, with a ring on the point (used to explain one mark)."""
    w, h = img.size
    cx, cy = grid_to_px(x, y, img.size)
    hw, hh = half * w / GRID, half * h / GRID * 1.4
    box = (max(0, cx - hw), max(0, cy - hh), min(w, cx + hw), min(h, cy + hh))
    out = img.convert("RGB").crop(tuple(round(v) for v in box))
    d = ImageDraw.Draw(out)
    r = max(8, round(w / 120))
    d.ellipse((cx - box[0] - r, cy - box[1] - r, cx - box[0] + r, cy - box[1] + r), outline=(255, 255, 0), width=3)
    return out.resize((out.width * 2, out.height * 2), Image.LANCZOS)


# ---------------------------------------------------------------- candle snapping

def _is_candle(p) -> bool:
    r, g, b = p[:3]
    red = r > 150 and r > g + 60 and r > b + 40
    green = g > 110 and g > r + 40 and g >= b - 40
    return red or green


def candle_extent(img: Image.Image, x: float, y: float, want: str, half_w: float = 9) -> float | None:
    """Looks at candle-coloured pixels in a narrow column around grid x and returns the grid y
    of the top ('top') or bottom ('bottom') of the candle cluster nearest to grid y.
    want='nearest': None if y is already on candles, else the closer edge of the nearest cluster."""
    w, h = img.size
    x0, x1 = max(0, round((x - half_w) * w / GRID)), min(w, round((x + half_w) * w / GRID))
    y0, y1 = round(0.05 * h), round(0.95 * h)            # skip volume strips / axis labels
    if x1 - x0 < 2:
        return None
    region = img.convert("RGB").crop((x0, y0, x1, y1))
    rw = region.width
    data = region.get_flattened_data() if hasattr(region, "get_flattened_data") else region.getdata()
    rows = [any(_is_candle(data[r * rw + c]) for c in range(rw)) for r in range(region.height)]
    runs, start, gap = [], None, max(4, round(0.025 * h))
    last = None
    for i, hit in enumerate(rows):
        if hit:
            if start is None:
                start = i
            elif last is not None and i - last > gap:
                runs.append((start, last))
                start = i
            last = i
    if start is not None:
        runs.append((start, last))
    runs = [(a, b) for a, b in runs if b - a >= 3]
    if not runs:
        return None
    target = y * h / GRID - y0
    a, b = min(runs, key=lambda ab: 0 if ab[0] <= target <= ab[1] else min(abs(target - ab[0]), abs(target - ab[1])))
    if want == "nearest":
        if a <= target <= b:
            return None
        edge = a if target < a else b
    else:
        edge = a if want == "top" else b
    return (edge + y0) * GRID / h


def snap_targets(img: Image.Image, spec: dict, max_move: float = 350) -> None:
    """Moves arrow targets of top/bottom-type callouts onto the nearest candle high/low.
    The original model coordinates are kept in callout['snapped'] for transparency."""
    for c in spec.get("callouts", []):
        t = c.get("type")
        want = "top" if t in TOP_TYPES else "bottom" if t in BOTTOM_TYPES else "nearest"
        y = candle_extent(img, c["target_x"], c["target_y"], want)
        if y is None or abs(y - c["target_y"]) > max_move:
            continue
        if want == "nearest":
            want = "bottom" if y < c["target_y"] else "top"   # candles above the target -> their bottom edge
        offset = -6 if want == "top" else 6   # stop just beyond the wick
        new_y = max(0, min(GRID, y + offset))
        if abs(new_y - c["target_y"]) >= 4:
            c["snapped"] = {"from_y": c["target_y"], "to_y": round(new_y, 1), "edge": want}
            for r in spec.get("rings", []):
                if math.dist((r["x"], r["y"]), (c["target_x"], c["target_y"])) < 30:
                    r["y"] = new_y
            c["target_y"] = new_y
    targets = [(c["target_x"], c["target_y"]) for c in spec.get("callouts", [])]
    for r in spec.get("rings", []):     # a ring on empty space, not attached to any arrow
        if any(math.dist((r["x"], r["y"]), t) < 30 for t in targets):
            continue
        y = candle_extent(img, r["x"], r["y"], "nearest")
        if y is not None and abs(y - r["y"]) <= max_move:
            r["snapped"] = {"from_y": r["y"], "to_y": round(y, 1)}
            r["y"] = y


# ---------------------------------------------------------------- annotation registry

def build_registry(spec: dict, image_size: tuple[int, int], original_size: tuple[int, int] | None = None) -> list[dict]:
    """Gives every zone, ring and callout a stable id and stores them in spec['annotations'].
    Callouts get a visible label (#1, #2, ...) and an id by type (H2_001, SELL_CLIMAX_001)."""
    per_type: dict[str, int] = {}
    reg = []
    for i, c in enumerate(spec.get("callouts", []), 1):
        t = c.get("type") or "other"
        per_type[t] = per_type.get(t, 0) + 1
        c["label"] = f"#{i}"
        c["annotation_id"] = f"{t.upper()}_{per_type[t]:03d}"
        px, py = grid_to_px(c["target_x"], c["target_y"], image_size)
        reg.append({"annotation_id": c["annotation_id"], "label": c["label"], "kind": "callout", "type": t,
                    "status": c.get("status", "candidate"), "text": c["text"], "reason": c.get("reason", ""),
                    "bar_ref": c.get("bar_ref", ""), "color": c.get("color", "white"),
                    "coordinates": {"grid": GRID, "x": c["target_x"], "y": c["target_y"],
                                    "label_x": c["label_x"], "label_y": c["label_y"]},
                    "pixel": {"x": round(px, 1), "y": round(py, 1)}, "image_size": list(image_size),
                    "original_size": list(original_size or image_size), "snapped": c.get("snapped"),
                    "evidence": [], "source_refs": []})
    for i, r in enumerate(spec.get("rings", []), 1):
        r["annotation_id"] = f"R{i}"
        near = min((a for a in reg if a["kind"] == "callout"),
                   key=lambda a: math.dist((a["coordinates"]["x"], a["coordinates"]["y"]), (r["x"], r["y"])),
                   default=None)
        linked = near["annotation_id"] if near and math.dist(
            (near["coordinates"]["x"], near["coordinates"]["y"]), (r["x"], r["y"])) < 45 else None
        reg.append({"annotation_id": r["annotation_id"], "label": r["annotation_id"], "kind": "ring",
                    "type": "ring", "status": "candidate", "linked_to": linked,
                    "coordinates": {"grid": GRID, "x": r["x"], "y": r["y"]}, "image_size": list(image_size)})
    for i, z in enumerate(spec.get("zones", []), 1):
        z["annotation_id"] = f"Z{i}"
        reg.append({"annotation_id": z["annotation_id"], "label": z["annotation_id"], "kind": "zone",
                    "type": z["kind"], "status": "candidate", "text": z.get("title", ""),
                    "coordinates": {"grid": GRID, "x1": z["x1"], "y1": z["y1"], "x2": z["x2"], "y2": z["y2"]},
                    "image_size": list(image_size)})
    spec["annotations"] = reg
    return reg


# ---------------------------------------------------------------- label layout

def callout_text(c: dict) -> str:
    """The text drawn for a callout (shared by layout and rendering so the boxes match)."""
    text = c["text"]
    if c.get("label"):
        text = f"{c['label']} {text}"
    if c.get("status") == "uncertain":
        text += " (?)"
    return text


def summary_text(spec: dict) -> str:
    summary = "\n".join(spec["summary_lines"]) if spec.get("summary_lines") else spec.get("summary", "")
    return "复盘总结 Review\n" + summary if summary else ""


class BusyMap:
    """Where the original chart already has ink (candles, wicks, text, EMA, buttons), on a coarse
    grid with an integral image, so 'how full is this box?' is a constant-time question."""

    def __init__(self, img: Image.Image, step: int = 4):
        self.step = step
        small = img.convert("RGB").resize((max(1, img.width // step), max(1, img.height // step)), Image.BILINEAR)
        w, h = small.size
        data = small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata()
        self.w, self.h = w, h
        ii = [[0] * (w + 1) for _ in range(h + 1)]
        for y in range(h):
            row, acc = ii[y + 1], 0
            above = ii[y]
            for x in range(w):
                r, g, b = data[y * w + x][:3]
                busy = (0.299 * r + 0.587 * g + 0.114 * b) > 75 or (max(r, g, b) - min(r, g, b)) > 60
                acc += busy
                row[x + 1] = above[x + 1] + acc
        self.ii = ii

    def fraction(self, box: tuple) -> float:
        x0, y0, x1, y1 = (max(0, min(int(v / self.step), lim)) for v, lim in
                          zip(box, (self.w, self.h, self.w, self.h)))
        if x1 <= x0 or y1 <= y0:
            return 1.0
        ii = self.ii
        total = ii[y1][x1] - ii[y0][x1] - ii[y1][x0] + ii[y0][x0]
        return total / ((x1 - x0) * (y1 - y0))


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _crosses(seg: tuple, box: tuple, samples: int = 12) -> bool:
    (x0, y0), (x1, y1) = seg
    return any(box[0] < x0 + (x1 - x0) * k / samples < box[2] and box[1] < y0 + (y1 - y0) * k / samples < box[3]
               for k in range(1, samples))


def layout_labels(chart: Image.Image, spec: dict) -> None:
    """Moves every callout label next to the bar it points at, into empty space, without covering
    the target, other labels, arrow tips or the summary box. The summary goes to the emptiest
    large area. The model's original positions are kept in label_from / summary_from."""
    w, h = chart.size
    unit = w / 2000
    pad = 6 * unit
    font_path = find_font()
    font = lambda size: (ImageFont.truetype(font_path, max(10, round(size * unit))) if font_path
                         else ImageFont.load_default())
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def box_size(text, size, max_w=None):
        f = font(size)
        if max_w:
            text = wrap_text(measure, text, f, max_w)
        b = measure.multiline_textbbox((0, 0), text, font=f, spacing=4)
        return b[2] - b[0] + 2 * pad, b[3] - b[1] + 2 * pad

    busy = BusyMap(chart)
    placed: list[tuple] = []
    margin = 0.01 * w
    inside = lambda b: b[0] >= margin and b[1] >= margin and b[2] <= w - margin and b[3] <= h - margin

    for zn in spec.get("zones", []):          # zone titles are drawn first by render(): keep them free
        if zn.get("title"):
            zw, zh = box_size(zn["title"], 20)
            x, y = min(zn["x1"], zn["x2"]) * w / GRID + 8 * unit, min(zn["y1"], zn["y2"]) * h / GRID + 8 * unit
            placed.append((x - pad, y - pad, x + zw - pad, y + zh - pad))
    tips = []
    for c in spec.get("callouts", []):         # arrow tips must stay visible
        tx, ty = c["target_x"] * w / GRID, c["target_y"] * h / GRID
        tips.append((tx - 14 * unit, ty - 14 * unit, tx + 14 * unit, ty + 14 * unit))

    text = summary_text(spec)
    if text:
        sw, sh = box_size(text, 16, 0.36 * w)
        want = (spec.get("summary_x", 790) * w / GRID, spec.get("summary_y", 140) * h / GRID)
        best = None
        for gy in range(0, 20):
            for gx in range(0, 20):
                x, y = margin + gx * (w - sw - 2 * margin) / 19, margin + gy * (h - sh - 2 * margin) / 19
                b = (x, y, x + sw, y + sh)
                cost = busy.fraction(b) * 10 + sum(_overlap(b, t) > 0 for t in tips) * 5 + \
                    sum(_overlap(b, p) for p in placed) / (sw * sh) * 10 + \
                    math.dist((x, y), want) / math.hypot(w, h) * 0.6
                if best is None or cost < best[0]:
                    best = (cost, b)
        b = best[1]
        if abs(b[0] - want[0]) > 2 or abs(b[1] - want[1]) > 2:
            spec["summary_from"] = [spec.get("summary_x"), spec.get("summary_y")]
        # render() draws the text at (x, y) and pads around it
        spec["summary_x"], spec["summary_y"] = (b[0] + pad) * GRID / w, (b[1] + pad) * GRID / h
        placed.append(b)

    arrows: list[tuple] = []                   # arrows already drawn: later labels avoid them
    directions = [(math.cos(a), math.sin(a)) for a in (math.radians(d) for d in range(0, 360, 30))]
    for i, c in enumerate(spec.get("callouts", [])):
        bw, bh = box_size(callout_text(c), 18)
        tx, ty = c["target_x"] * w / GRID, c["target_y"] * h / GRID
        others = [t for j, t in enumerate(tips) if j != i]
        cands = [(c["label_x"] * w / GRID - pad, c["label_y"] * h / GRID - pad)]   # the model's own choice
        for r in (40, 80, 130, 190, 260, 340, 440):
            for dx, dy in directions:
                cx = tx + dx * (r * unit + bw / 2)
                cy = ty + dy * (r * unit + bh / 2)
                cands.append((cx - bw / 2, cy - bh / 2))
        best = None
        for x, y in cands:
            b = (x, y, x + bw, y + bh)
            if not inside(b):
                continue
            covers_target = b[0] - 10 * unit < tx < b[2] + 10 * unit and b[1] - 10 * unit < ty < b[3] + 10 * unit
            if covers_target:
                continue
            crowd = sum(_overlap(b, p) for p in placed) / (bw * bh)
            cx, cy = min(max(tx, b[0]), b[2]), min(max(ty, b[1]), b[3])     # arrow start on the label edge
            dist = math.dist((cx, cy), (tx, ty)) / math.hypot(w, h)
            seg = ((cx, cy), (tx, ty))
            crossing = sum(_crosses(a, b) for a in arrows) + sum(_crosses(seg, p) for p in placed)
            cost = busy.fraction(b) * 6 + crowd * 12 + sum(_overlap(b, t) > 0 for t in others) * 2 + dist * 5 +                 crossing * 1.5
            if best is None or cost < best[0]:
                best = (cost, b, seg)
        if best is None:
            continue
        b = best[1]
        arrows.append(best[2])
        nx, ny = (b[0] + pad) * GRID / w, (b[1] + pad) * GRID / h
        if abs(nx - c["label_x"]) > 1 or abs(ny - c["label_y"]) > 1:
            c["label_from"] = [c["label_x"], c["label_y"]]
            c["label_x"], c["label_y"] = round(nx, 1), round(ny, 1)
        placed.append(b)
    for a in spec.get("annotations", []):       # keep the registry in sync with the new label spots
        if a.get("kind") == "callout":
            c = next((c for c in spec["callouts"] if c.get("annotation_id") == a["annotation_id"]), None)
            if c:
                a["coordinates"].update(label_x=c["label_x"], label_y=c["label_y"])


def finalize_spec(chart: Image.Image, spec: dict, snap: bool = True,
                  original_size: tuple[int, int] | None = None, layout: bool = True) -> dict:
    """Post-processing after the model: clamp coordinates, snap arrows, build the registry,
    place labels next to their bars."""
    for c in spec.get("callouts", []):
        for k in ("target_x", "target_y", "label_x", "label_y"):
            c[k] = max(0.0, min(float(GRID), float(c[k])))
    if snap:
        snap_targets(chart, spec)
    if "summary_lines" in spec and not spec.get("summary"):
        spec["summary"] = "\n".join(spec["summary_lines"])
    build_registry(spec, chart.size, original_size)
    if layout:
        layout_labels(chart, spec)
    return spec


# ---------------------------------------------------------------- drawing

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> str:
    """Wraps text (Chinese has no spaces, so wrap per character) to fit max_w pixels."""
    out = []
    for para in text.split("\n"):
        line = ""
        for ch in para:
            if line and draw.textlength(line + ch, font=font) > max_w:
                out.append(line)
                line = "  " + ch if ch != " " else "  "
            else:
                line += ch
        out.append(line)
    return "\n".join(out)


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

    def label(x, y, text, color, size=19, bg=(10, 12, 20, 210), max_w=None):
        f = font(size)
        pad = 6 * unit
        if max_w:
            text = wrap_text(d, text, f, max_w)
        tb = d.multiline_textbbox((0, 0), text, font=f, spacing=4)
        bw, bh = tb[2] - tb[0], tb[3] - tb[1]
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
        # offset by the text's own bbox origin so the text sits exactly inside the box
        d.multiline_text((x - tb[0], y - tb[1]), text, font=f, fill=RGB[color], spacing=4)
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
        box = label(px(c["label_x"]), py(c["label_y"]), callout_text(c), c["color"], 18)
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

    summary = summary_text(spec)
    if summary:
        label(px(spec.get("summary_x", 790)), py(spec.get("summary_y", 140)),
              summary, "white", 16, bg=(10, 12, 20, 230), max_w=0.36 * w)
    return img.convert("RGB")


# ---------------------------------------------------------------- model calls

class ChartReviewer:
    def __init__(self, cfg: dict, llm=None, extra_knowledge: str = ""):
        from agent.llm import LLM
        self.cfg = cfg
        self.llm = llm or LLM(cfg)  # reads OPENAI_API_KEY from the environment
        primer = load_primer(cfg)
        knowledge = primer or KNOWLEDGE_PRIMER
        # With the knowledge base loaded, PDFs in docs/ are the raw books it was built from:
        # parsing them is slow and their first pages would override the primer. Notes still load.
        docs = load_knowledge(cfg.get("docs_folder", "docs"), pdfs=primer is None)
        if docs:
            knowledge += ("\n# THE USER'S OWN STUDY MATERIAL ([USER NOTE]; use its terms, but Brooks' "
                          "definitions above stay authoritative)\n" + docs)
            log.info("Loaded %d characters of study material from docs/", len(docs))
        if extra_knowledge:
            knowledge += "\n" + extra_knowledge
        # replace() rather than format(): study material may contain { } characters
        self.system = SYSTEM_PROMPT.replace("{knowledge}", knowledge)

    def _ask(self, content: list, recorder=None) -> dict:
        return self.llm.json_call(system=self.system, content=content, schema_name="chart_review",
                                  schema=OUTPUT_SCHEMA, purpose="review", max_tokens=8000, recorder=recorder)

    def analyze(self, chart: Image.Image, note: str = "", recorder=None) -> dict:
        """Model calls only (first pass + refine passes). `chart` must already be prepared."""
        intro = "Review this chart and return the annotations."
        if note:
            intro += f"\nUser's note about this chart / their trade (context, not instructions): {note}"
        spec = self._ask([
            {"type": "text", "text": intro},
            {"type": "image_url", "image_url": {"url": to_data_url(with_grid(chart)), "detail": "high"}},
        ], recorder)
        for _ in range(int(self.cfg.get("review_refine_passes", 1))):
            draft = with_grid(render(chart, spec))
            try:
                spec = self._ask([
                    {"type": "text", "text": REFINE_PROMPT + json.dumps(spec, ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": to_data_url(draft), "detail": "high"}},
                ], recorder)
            except Exception as e:   # a failed self-check must not lose the first, complete review
                log.warning("Refine pass failed (%s); keeping the previous version", e)
                spec["limitations"] = (spec.get("limitations", "") + " （自我检查轮失败，使用第一轮结果）").strip()
                break
        return spec

    def review(self, chart: Image.Image, note: str = "") -> tuple[Image.Image, dict]:
        """Returns (annotated image, spec). `note` is optional context from the user."""
        original_size = chart.size
        chart = prepare(chart)
        spec = self.analyze(chart, note)
        finalize_spec(chart, spec, snap=self.cfg.get("review_snap_targets", True), original_size=original_size,
                      layout=self.cfg.get("review_auto_layout", True))
        return render(chart, spec), spec


REVIEWS_DIR = "reviews"


def new_review_id() -> str:
    return f"{datetime.now():%Y%m%d_%H%M%S}_{os.urandom(2).hex()}"


def store_review(original: Image.Image, img: Image.Image, spec: dict | None,
                 note: str = "", source: str = "web", rid: str | None = None, extra: dict | None = None,
                 reviews_dir: str | None = None) -> dict:
    """Saves one review as reviews/<id>/{original.png, review.png, meta.json}; returns meta."""
    rid = rid or new_review_id()
    folder = os.path.join(reviews_dir or REVIEWS_DIR, rid)
    os.makedirs(folder, exist_ok=True)
    prepare(original).save(os.path.join(folder, "original.png"))
    img.save(os.path.join(folder, "review.png"))
    spec = spec or {}
    meta = {
        "id": rid,
        "created": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "note": note,
        "description": spec.get("chart_description", ""),
        "summary": spec.get("summary") or "\n".join(spec.get("summary_lines", [])),
        "analysis": spec.get("analysis", ""),
        "market_context": spec.get("market_context", ""),
        "always_in": spec.get("always_in", ""),
        "current_state": spec.get("current_state", ""),
        "limitations": spec.get("limitations", ""),
        "annotations": [{k: a.get(k) for k in ("annotation_id", "label", "type", "status", "text", "kind")}
                        for a in spec.get("annotations", []) if a.get("kind") == "callout"],
        **(extra or {}),
        "spec": spec,
    }
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def list_reviews(limit: int = 200, reviews_dir: str | None = None) -> list[dict]:
    """Newest first. Each item is the review's meta without the bulky spec."""
    out = []
    pattern = os.path.join(reviews_dir or REVIEWS_DIR, "*", "meta.json")
    for meta_path in sorted(glob.glob(pattern), reverse=True)[:limit]:
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
        spec = spec.get("spec", spec)   # accept a saved meta.json too
        prepared = prepare(chart)
        if "annotations" not in spec:
            build_registry(spec, prepared.size, chart.size)
        img = render(prepared, spec)
    else:
        img, spec = ChartReviewer(cfg).review(chart, args.note)
    meta = store_review(chart, img, spec, args.note, source="cli")
    print(meta["analysis"])
    print(f"\nSaved: {os.path.join(REVIEWS_DIR, meta['id'], 'review.png')}")


if __name__ == "__main__":
    main()
