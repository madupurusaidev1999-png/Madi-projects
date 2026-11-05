"""Persistent state, result tracking (TP/SL hits) and the daily report.

Results feed back into strategy.trigger_weight(), so triggers that work
get more weight over time and ones that fail get less.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from . import config
from .telegram import format_outcome

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(config.STATE_DIR, "state.json")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"signals": [], "trigger_stats": {}, "last_batch": None, "last_report_day": None}


def save_state(state):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    # keep 14 days of history
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    state["signals"] = [s for s in state["signals"] if s["sent_at"] >= cutoff]
    with open(STATE_FILE + ".tmp", "w") as f:
        json.dump(state, f, indent=1)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)


def _record_trigger(state, rec, win):
    for t in rec.get("triggers", []):
        s = state["trigger_stats"].setdefault(t, {"w": 0, "l": 0})
        s["w" if win else "l"] += 1


def update_open(state, candles_by_id, now=None):
    """Check open signals against new candles. Returns outcome messages."""
    now = now or datetime.now(timezone.utc)
    msgs = []
    for rec in state["signals"]:
        if rec["status"] not in ("open", "tp1"):
            continue
        df = candles_by_id.get(rec["asset"]["id"])
        sent = datetime.fromisoformat(rec["sent_at"])
        d = 1 if rec["direction"] == "LONG" else -1
        risk = abs(rec["entry"] - rec["sl"])
        if df is not None and len(df):
            after = df[df.index >= datetime.fromisoformat(rec["candle_time"]) + timedelta(minutes=15)]
            after = after[after.index > datetime.fromisoformat(rec.get("checked_to", "1970-01-01T00:00:00+00:00"))]
            for ts, c in after.iterrows():
                hi, lo = c["high"], c["low"]
                fav, adv = (hi, lo) if d == 1 else (lo, hi)
                if rec["status"] == "open":
                    if (adv - rec["sl"]) * d <= 0:  # stop touched (checked first = conservative)
                        rec.update(status="sl", result_r=-1.0, closed_at=ts.isoformat())
                        _record_trigger(state, rec, False)
                        msgs.append(format_outcome(rec, "sl", -1.0))
                        break
                    if (fav - rec["tp1"]) * d >= 0:
                        rec.update(status="tp1", result_r=config.TP1_R)
                        _record_trigger(state, rec, True)
                        msgs.append(format_outcome(rec, "tp1", config.TP1_R))
                if rec["status"] == "tp1":
                    if (fav - rec["tp2"]) * d >= 0:
                        rec.update(status="tp2", result_r=config.TP2_R, closed_at=ts.isoformat())
                        msgs.append(format_outcome(rec, "tp2", config.TP2_R))
                        break
                    if (adv - rec["entry"]) * d <= 0:  # stop moved to break-even after TP1
                        rec.update(status="tp1_be", closed_at=ts.isoformat())
                        break
            if len(after):
                rec["checked_to"] = after.index[-1].isoformat()

        if rec["status"] in ("open", "tp1") and now - sent > timedelta(hours=config.SIGNAL_EXPIRY_HOURS):
            if rec["status"] == "open":
                last = float(df["close"].iloc[-1]) if df is not None and len(df) else rec["entry"]
                r = round((last - rec["entry"]) * d / risk, 2) if risk else 0.0
                rec.update(status="expired", result_r=r, closed_at=now.isoformat())
                _record_trigger(state, rec, r > 0)
                msgs.append(format_outcome(rec, "expired", r))
            else:
                rec.update(status="tp1_done", closed_at=now.isoformat())
    return msgs


def daily_report(state, now=None):
    """Once per day (first run after midnight UTC) summarise yesterday."""
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    if state.get("last_report_day") == today:
        return None, None
    first_run = state.get("last_report_day") is None
    state["last_report_day"] = today
    if first_run:
        return None, None
    day = (now.date() - timedelta(days=1)).isoformat()
    recs = [s for s in state["signals"] if s["sent_at"][:10] == day]
    if not recs:
        return None, None
    closed = [s for s in recs if "result_r" in s]
    wins = [s for s in closed if s["result_r"] > 0]
    total_r = sum(s["result_r"] for s in closed)
    by_grade = {}
    for s in closed:
        g = by_grade.setdefault(s["grade"], [0, 0])
        g[0 if s["result_r"] > 0 else 1] += 1

    lines = [f"📊 <b>Daily report {day}</b>",
             f"Signals: {len(recs)} · closed: {len(closed)} · open: {len(recs) - len(closed)}"]
    if closed:
        lines.append(f"Win rate: {len(wins) / len(closed) * 100:.0f}% · total {total_r:+.1f}R")
        lines.append("By grade: " + ", ".join(f"{g} {w}W/{l}L" for g, (w, l) in sorted(by_grade.items())))
    best = max(closed, key=lambda s: s["result_r"], default=None)
    if best:
        lines.append(f"Best: {best['direction']} {best['symbol']} {best['result_r']:+.1f}R")
    ts = state.get("trigger_stats", {})
    if ts:
        lines.append("Trigger record (all time): " + ", ".join(
            f"{k} {v['w']}W/{v['l']}L" for k, v in sorted(ts.items())))
    text = "\n".join(lines)

    md = [f"# Signal report {day}", "", text.replace("<b>", "**").replace("</b>", "**"), "",
          "| Time (UTC) | Pair | Side | Grade | Score | Entry | Result |", "|---|---|---|---|---|---|---|"]
    for s in recs:
        md.append(f"| {s['sent_at'][11:16]} | {s['symbol']} | {s['direction']} | {s['grade']} | "
                  f"{s['score']:.0f} | {s['entry']:.6g} | {s['status']} {s.get('result_r', '')} |")
    return text, (day, "\n".join(md) + "\n")
