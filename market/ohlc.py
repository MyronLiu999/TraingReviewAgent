"""OHLC path: load bars from CSV, cut them at the analysis time, and compute facts the model
must not guess (swing points, EMA, bar counts, ranges).

No future data: everything after `as_of` is dropped before any analysis, and the number of
dropped bars is reported. The H/L counter follows the counting rules summarized in the
knowledge base (03a §Counting Algorithm, from [TR p.29, p.128, p.519][Trends p.65-66]
[Course2018 p.528, p.534, p.563]); it is a [DERIVED INTERPRETATION] for bar-level checks, not
a replacement for reading the context.
"""

import csv
import io
from dataclasses import dataclass
from datetime import datetime

TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%m/%d/%Y %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d")


@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def parse_time(s: str) -> datetime:
    s = s.strip().replace("Z", "")
    if s.isdigit():                                   # unix seconds / milliseconds
        v = int(s)
        return datetime.fromtimestamp(v / 1000 if v > 10**11 else v)
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized time: {s!r}")


def load_csv(source: str) -> list[Bar]:
    """`source` is a path or CSV text with columns time/date, open, high, low, close[, volume]."""
    text = open(source, encoding="utf-8-sig").read() if "\n" not in source and source.endswith(".csv") else source
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    if not rows:
        raise ValueError("CSV has no rows")
    keys = {k.lower().strip(): k for k in rows[0]}
    tkey = next((keys[k] for k in ("time", "datetime", "date", "timestamp") if k in keys), None)
    need = [keys.get(k) for k in ("open", "high", "low", "close")]
    if not tkey or None in need:
        raise ValueError("CSV needs time, open, high, low, close columns")
    bars = []
    for r in rows:
        o, h, l, c = (float(r[k]) for k in need)
        if h < max(o, c) or l > min(o, c) or h < l:
            raise ValueError(f"Bad bar at {r[tkey]}: high/low do not contain open/close")
        bars.append(Bar(parse_time(r[tkey]), o, h, l, c, float(r.get(keys.get("volume", ""), 0) or 0)))
    bars.sort(key=lambda b: b.time)
    return bars


def cut_at(bars: list[Bar], as_of: datetime | None) -> tuple[list[Bar], int]:
    """Bars up to and including as_of, plus how many later bars were dropped."""
    if as_of is None:
        return bars, 0
    kept = [b for b in bars if b.time <= as_of]
    return kept, len(bars) - len(kept)


def ema(values: list[float], n: int = 20) -> list[float]:
    out, k = [], 2 / (n + 1)
    for i, v in enumerate(values):
        out.append(v if i == 0 else v * k + out[-1] * (1 - k))
    return out


def swings(bars: list[Bar], n: int = 2) -> dict:
    highs, lows = [], []
    for i in range(n, len(bars) - n):
        win = bars[i - n:i + n + 1]
        if bars[i].high == max(b.high for b in win):
            highs.append((i, bars[i].high))
        if bars[i].low == min(b.low for b in win):
            lows.append((i, bars[i].low))
    return {"highs": highs, "lows": lows}


def count_pullback(bars: list[Bar], side: str = "bull") -> list[dict]:
    """Labels H1..H4 (bull pullbacks) or L1..L4 (bear rallies).

    bull: a pullback starts with a bar whose low is below the prior bar's low; inside it, the
    first bar with a high above the prior bar's high is H1 (signal bar = the prior bar). A later
    H(n+1) needs at least one lower-high bar first (two rising bars in a row are not an H2).
    A new high above the trend extreme resets the count; beyond H4, stop counting.
    `bear` is the exact mirror (L1..L4)."""
    events = []
    if len(bars) < 3:
        return events
    up = side == "bull"
    hi = (lambda b: b.high) if up else (lambda b: -b.low)
    lo = (lambda b: b.low) if up else (lambda b: -b.high)
    extreme = hi(bars[0])
    in_pullback, count, need_lower_high = False, 0, False
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        if not in_pullback:
            if hi(b) > extreme:
                extreme = hi(b)
            if lo(b) < lo(p):
                in_pullback, count, need_lower_high = True, 0, False
            continue
        if hi(b) > extreme:                       # new trend extreme: pullback over, reset
            extreme, in_pullback, count = hi(b), False, 0
            continue
        if hi(b) > hi(p) and not need_lower_high:
            count += 1
            if count > 4:
                events.append({"index": i, "label": "stop counting (beyond 4)", "time": b.time.isoformat()})
                in_pullback = False
                continue
            label = ("H" if up else "L") + str(count)
            events.append({"index": i, "label": label, "signal_bar": i - 1, "entry_bar": i,
                           "time": b.time.isoformat(), "entry_price": p.high if up else p.low})
            need_lower_high = True
        elif hi(b) < hi(p):
            need_lower_high = False
    return events


def summarize(bars: list[Bar], last_n: int = 30) -> dict:
    """Facts for the model, all computed from the bars (never from the screenshot)."""
    if not bars:
        return {"bars": 0}
    closes = [b.close for b in bars]
    e = ema(closes)
    tr = [max(b.high, p.close) - min(b.low, p.close) for p, b in zip(bars, bars[1:])] or [bars[0].high - bars[0].low]
    atr = sum(tr[-14:]) / len(tr[-14:])
    sw = swings(bars)
    tail = bars[-last_n:]
    return {
        "bars": len(bars), "first_time": bars[0].time.isoformat(), "last_time": bars[-1].time.isoformat(),
        "last_close": bars[-1].close, "session_high": max(b.high for b in bars),
        "session_low": min(b.low for b in bars), "ema20": round(e[-1], 4), "atr14": round(atr, 4),
        "close_vs_ema": "above" if closes[-1] > e[-1] else "below",
        "recent_swing_highs": [{"time": bars[i].time.isoformat(), "price": v} for i, v in sw["highs"][-4:]],
        "recent_swing_lows": [{"time": bars[i].time.isoformat(), "price": v} for i, v in sw["lows"][-4:]],
        "bull_counts": [x for x in count_pullback(bars, "bull") if x["index"] >= len(bars) - last_n],
        "bear_counts": [x for x in count_pullback(bars, "bear") if x["index"] >= len(bars) - last_n],
        "last_bars": [[b.time.strftime("%m-%d %H:%M"), b.open, b.high, b.low, b.close] for b in tail],
    }


def price_plausible(price: float, bars: list[Bar], atr_mult: float = 4.0) -> bool:
    if not bars:
        return True
    s = summarize(bars, 5)
    pad = s["atr14"] * atr_mult
    return s["session_low"] - pad <= price <= s["session_high"] + pad
