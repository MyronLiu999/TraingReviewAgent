"""Draws a trade plan on the user's screenshot: entry / stop / targets / key levels as horizontal
lines, the risk (red) and reward (green) zones, the signal bar, and a header with the call.

The model gives each level a y position on the 0-1000 grid and (when readable) a price. Code checks
the geometry instead of trusting it:
- levels with a price but no y are placed with a linear price -> y fit from the levels that have both
  (needs two different prices, and a higher price must be higher on the screen)
- for a LONG, the target must be above the entry and the stop below it on the screen (mirrored for a
  SHORT); otherwise the trade lines are not drawn and an issue is reported
"""

from PIL import Image, ImageDraw, ImageFont

import chart_review as cr

COLORS = {"entry": (90, 170, 255), "stop": (255, 90, 90), "target": (80, 230, 120), "runner": (40, 170, 90),
          "key": (200, 200, 200)}
HEADER = {"TAKE": (40, 160, 80), "WAIT": (200, 150, 30), "NO_TRADE": (150, 60, 60)}


def _levels(r: dict) -> list[dict]:
    out = []
    for kind, key, name in (("entry", "entry", "入场 Entry"), ("stop", "stop", "止损 Stop"),
                            ("target", "target", "目标1 T1"), ("runner", "runner_target", "目标2 T2")):
        leg = r.get(key) or {}
        if leg.get("y") is None and leg.get("price") is None:
            continue
        if kind == "runner" and leg.get("price") is not None and leg.get("price") == (r.get("target") or {}).get("price"):
            continue                                   # runner repeated as the first target
        out.append({"kind": kind, "label": name, "price": leg.get("price"), "y": leg.get("y")})
    for lv in (r.get("key_levels") or [])[:4]:
        if lv.get("y") is not None or lv.get("price") is not None:
            out.append({"kind": "key", "label": lv.get("label") or "level", "price": lv.get("price"), "y": lv.get("y")})
    return out


def fill_positions(levels: list[dict]) -> list[str]:
    """Places levels that have a price but no y, using a least-squares price -> y line."""
    issues = []
    pts = [(lv["price"], lv["y"]) for lv in levels if lv["price"] is not None and lv["y"] is not None]
    if len({p for p, _ in pts}) >= 2:
        n = len(pts)
        mx, my = sum(p for p, _ in pts) / n, sum(y for _, y in pts) / n
        var = sum((p - mx) ** 2 for p, _ in pts)
        a = sum((p - mx) * (y - my) for p, y in pts) / var
        b = my - a * mx
        if a >= 0:
            issues.append("price/position of the levels are inconsistent (a higher price is not higher on the chart)")
            for lv in levels:
                lv["y"] = None
            return issues
        for lv in levels:
            if lv["y"] is None and lv["price"] is not None:
                y = a * lv["price"] + b
                lv["y"] = y if 0 <= y <= 1000 else None
    return issues


def check_geometry(levels: list[dict], bias: str) -> list[str]:
    ys = {lv["kind"]: lv["y"] for lv in levels if lv["y"] is not None and lv["kind"] != "key"}
    e = ys.get("entry")
    if e is None or bias not in ("LONG", "SHORT"):
        return []
    up = bias == "LONG"
    bad = [k for k in ("target", "runner") if k in ys and ((ys[k] >= e) if up else (ys[k] <= e))]
    if "stop" in ys and ((ys["stop"] <= e) if up else (ys["stop"] >= e)):
        bad.append("stop")
    if bad:
        for lv in levels:
            if lv["kind"] != "key":
                lv["y"] = None
        return [f"trade lines not drawn: {', '.join(bad)} on the wrong side of the entry on the chart"]
    return []


def render_plan(chart: Image.Image, r: dict, headline: str = "") -> tuple[Image.Image, list[str]]:
    """Returns (image with the plan drawn on it, issues)."""
    img = chart.convert("RGBA")
    w, h = img.size
    unit = max(w, 800) / 2000
    font_path = cr.find_font()
    font = lambda s: ImageFont.truetype(font_path, max(10, round(s * unit))) if font_path else ImageFont.load_default()
    levels = _levels(r)
    issues = fill_positions(levels)
    issues += check_geometry(levels, r.get("bias", "WAIT"))
    py = lambda v: v * h / 1000
    sx = r.get("signal_bar", {}).get("x")
    x0 = (sx * w / 1000) if sx is not None else 0.55 * w

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ys = {lv["kind"]: py(lv["y"]) for lv in levels if lv["y"] is not None and lv["kind"] != "key"}
    if "entry" in ys:
        for k, color in (("stop", (255, 90, 90, 55)), ("target", (80, 230, 120, 45))):
            if k in ys:
                d.rectangle((x0, min(ys["entry"], ys[k]), w, max(ys["entry"], ys[k])), fill=color)
    img = Image.alpha_composite(img, layer)
    d = ImageDraw.Draw(img)
    width = max(2, round(3 * unit))
    f = font(19)
    placed: list[tuple] = []
    for lv in sorted(levels, key=lambda v: v["kind"] == "key"):          # trade lines on top
        if lv["y"] is None:
            continue
        y = py(lv["y"])
        color = COLORS[lv["kind"]]
        start = 0 if lv["kind"] == "key" else x0
        if lv["kind"] == "key":                                         # dashed
            step = 18 * unit
            x = start
            while x < w:
                d.line([(x, y), (min(x + step * 0.6, w), y)], fill=color, width=max(1, width - 1))
                x += step
        else:
            d.line([(start, y), (w, y)], fill=color, width=width)
        text = lv["label"] + (f"  {lv['price']:g}" if isinstance(lv["price"], (int, float)) else "")
        tb = d.textbbox((0, 0), text, font=f)
        bw, bh = tb[2] - tb[0], tb[3] - tb[1]
        pad = 5 * unit
        tx = max(x0 + pad, 0.94 * w - bw) if lv["kind"] != "key" else 0.94 * w - bw   # left of the price axis
        ty = min(max(y - bh - 2 * pad, pad), h - bh - 2 * pad)
        for _ in range(8):                                              # keep labels from overlapping
            box = (tx - pad, ty - pad, tx + bw + pad, ty + bh + pad)
            if not any(not (box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3]) for p in placed):
                break
            ty += bh + 2 * pad
        placed.append(box)
        d.rounded_rectangle(box, radius=5 * unit, fill=(10, 12, 20), outline=color, width=max(1, round(2 * unit)))
        d.text((tx - tb[0], ty - tb[1]), text, font=f, fill=color)
    if sx is not None and "entry" in ys:
        rad = 16 * unit
        d.ellipse((x0 - rad, ys["entry"] - rad, x0 + rad, ys["entry"] + rad), outline=COLORS["entry"], width=width)
    if headline:
        hf = font(24)
        text = cr.wrap_text(d, headline, hf, 0.6 * w)
        tb = d.multiline_textbbox((0, 0), text, font=hf, spacing=4)
        pad = 8 * unit
        box = (pad, pad, pad + tb[2] - tb[0] + 2 * pad, pad + tb[3] - tb[1] + 2 * pad)
        d.rounded_rectangle(box, radius=6 * unit, fill=HEADER.get(r.get("recommendation"), (60, 60, 60)))
        d.multiline_text((2 * pad - tb[0], 2 * pad - tb[1]), text, font=hf, fill=(255, 255, 255), spacing=4)
    return img.convert("RGB"), issues
