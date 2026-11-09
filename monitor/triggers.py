"""Checks a trade plan against new closed bars - code only, no model.

Plan = the validated decision (bias, recommendation, entry{order_type, price}, stop{price},
target{price}, runner_target{price}). State moves:  pending -> in_trade -> done,  or pending -> cancelled.

- stop entry:  LONG triggers when a bar trades at/above the entry price, SHORT at/below
- limit entry: LONG triggers when a bar trades at/below the entry price, SHORT at/above
- market entry: triggers on the first new bar (at its open)
- before the entry, a bar through the stop cancels the setup (the signal failed)
- in the trade: stop and target are checked per bar; when one bar touches both, the stop is assumed
  first (conservative; a single bar cannot tell the order)
- at target 1 half the position is taken off and the stop moves to breakeven; the rest runs to the
  runner target (if there is one) - result_r is the average of the two halves
Results are in R (risk = |entry - stop|). These are bar-level approximations without slippage.
"""

from market.ohlc import Bar


def new_plan_state(plan: dict) -> dict:
    ok = (plan.get("bias") in ("LONG", "SHORT") and plan.get("recommendation") in ("TAKE", "WAIT")
          and plan.get("entry", {}).get("price") is not None and plan.get("stop", {}).get("price") is not None)
    return {"status": "pending" if ok else "inactive", "entry_time": None, "fill": None, "exit_time": None,
            "result_r": None, "partial_done": False, "partial_r": None}


def _ev(kind: str, bar: Bar, price, note: str = "") -> dict:
    return {"type": kind, "time": bar.time.isoformat(), "price": price, "note": note}


def check_plan(plan: dict, bars: list[Bar], state: dict) -> list[dict]:
    """Advances `state` (in place) over `bars`; returns the events that happened."""
    events: list[dict] = []
    if state.get("status") not in ("pending", "in_trade"):
        return events
    long = plan["bias"] == "LONG"
    e, s = plan["entry"]["price"], plan["stop"]["price"]
    t = (plan.get("target") or {}).get("price")
    t2 = (plan.get("runner_target") or {}).get("price")
    order = plan["entry"].get("order_type") or "stop"
    risk = abs(e - s) or None
    above = lambda bar, p: bar.high >= p
    below = lambda bar, p: bar.low <= p
    through = (lambda b, p: below(b, p)) if long else (lambda b, p: above(b, p))    # against the trade
    reaches = (lambda b, p: above(b, p)) if long else (lambda b, p: below(b, p))    # in the trade direction

    def r_of(price):
        if not risk:
            return None
        return round(((price - state["fill"]) if long else (state["fill"] - price)) / risk, 2)

    for bar in bars:
        if state["status"] == "pending":
            if order == "market":
                filled, fill = True, bar.open
            elif order == "limit":
                filled, fill = through(bar, e), e
            else:
                filled, fill = reaches(bar, e), e
            if not filled:
                if through(bar, s):
                    state["status"] = "cancelled"
                    events.append(_ev("cancelled", bar, s, "price went through the stop before the entry triggered"))
                    break
                continue
            state.update(status="in_trade", entry_time=bar.time.isoformat(), fill=fill)
            events.append(_ev("entry_triggered", bar, fill))
            # the entry bar itself can also reach the stop or the target (checked below)
        if state["status"] != "in_trade":
            continue
        cur_stop = state["fill"] if state["partial_done"] else s          # breakeven after target 1
        stop_hit = through(bar, cur_stop)
        tgt_hit = t is not None and not state["partial_done"] and reaches(bar, t)
        if stop_hit:
            r = r_of(cur_stop)
            if state["partial_done"] and r is not None and state["partial_r"] is not None:
                r = round((state["partial_r"] + r) / 2, 2)
            state.update(status="done", exit_time=bar.time.isoformat(), result_r=r)
            if tgt_hit:
                note = "stop and target in the same bar: stop assumed first"
            else:
                note = "rest stopped at breakeven" if state["partial_done"] else ""
            events.append(_ev("breakeven_stop" if state["partial_done"] else "stop_hit", bar, cur_stop, note))
            break
        if tgt_hit:
            state.update(partial_done=True, partial_r=r_of(t))
            events.append(_ev("target_hit", bar, t, f"{r_of(t)}R"))
            if t2 is None or t2 == t:
                state.update(status="done", exit_time=bar.time.isoformat(), result_r=r_of(t))
                break
            continue
        if state["partial_done"] and t2 is not None and t2 != t and reaches(bar, t2):
            r2 = r_of(t2)
            r = round((state["partial_r"] + r2) / 2, 2) if r2 is not None and state["partial_r"] is not None else None
            state.update(status="done", exit_time=bar.time.isoformat(), result_r=r)
            events.append(_ev("runner_target_hit", bar, t2, f"{r2}R"))
            break
    return events
