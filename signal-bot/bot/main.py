"""Entry point. Run every 5 minutes (GitHub Actions does this).

  * every run:   scan all markets + sentiment, track open signals,
                 send URGENT signals (score >= config.URGENT_SCORE)
  * every 15 min: send the best 1-3 signals of the moment
  * once a day:  send a performance report

Usage:
  python -m bot.main            # normal run
  python -m bot.main --test     # send a test message to Telegram
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from . import config, markets, telegram, tracker
from .markets import Asset
from .sentiment import Sentiment
from .strategy import analyze, apply_sentiment, grade

log = logging.getLogger("bot")


def build_universe(state):
    assets = []
    try:
        assets += markets.crypto_universe()
    except Exception as e:
        log.error("crypto universe failed: %s", e)
    assets += markets.metal_universe()
    # keep tracking pairs with open signals even if they dropped out of the top list
    have = {a.id for a in assets}
    for rec in state["signals"]:
        if rec["status"] in ("open", "tp1") and rec["asset"]["id"] not in have:
            assets.append(Asset.from_dict(rec["asset"]))
            have.add(rec["asset"]["id"])
    return assets


def fetch_all(assets):
    candles = {}
    for a in assets:
        try:
            df = markets.candles_15m(a)
            if len(df) and datetime.now(timezone.utc) - df.index[-1] > timedelta(hours=config.MAX_DATA_AGE_HOURS):
                log.info("skip %s: market closed / stale data", a.symbol)
                continue
            candles[a.id] = df
        except Exception as e:
            log.warning("candles failed for %s: %s", a.symbol, e)
        time.sleep({"kraken_spot": 0.5, "yahoo": 1.5}.get(a.source, 0.2))  # stay under rate limits
    return candles


def select(cands, state, now, batch_due):
    """Pick what to send this run."""
    recent = [s for s in state["signals"]
              if now - datetime.fromisoformat(s["sent_at"]) < timedelta(hours=config.COOLDOWN_HOURS)]
    blocked = {(s["asset"]["id"], s["direction"]) for s in recent}
    blocked |= {(s["asset"]["id"], d) for s in state["signals"]
                if s["status"] in ("open", "tp1") for d in ("LONG", "SHORT")}

    pool, used = [], set()
    for asset, sig in sorted(cands, key=lambda c: c[1]["score"], reverse=True):
        if (asset.id, sig["direction"]) in blocked or asset.id in used or not sig["grade"]:
            continue
        used.add(asset.id)
        pool.append((asset, sig))

    last_hour = sum(1 for s in state["signals"]
                    if now - datetime.fromisoformat(s["sent_at"]) < timedelta(hours=1))
    room = max(0, config.MAX_PER_HOUR - last_hour)

    urgent = [c for c in pool if c[1]["score"] >= config.URGENT_SCORE]
    if not batch_due:
        return urgent[: min(1, room)], True
    picked = [c for c in pool if c[1]["score"] >= config.GRADE_B][: config.MAX_PER_BATCH]
    if len(picked) < config.MIN_PER_BATCH:
        picked = pool[: config.MIN_PER_BATCH]  # best grade-C if nothing better
    return picked[: max(room, config.MIN_PER_BATCH)], False


def run():
    now = datetime.now(timezone.utc)
    state = tracker.load_state()

    assets = build_universe(state)
    log.info("scanning %d markets", len(assets))
    for a in assets:
        if a.kind == "metal":
            log.info("  metal %-28s via %s (%s)", a.symbol, a.source, a.ref)
    candles = fetch_all(assets)

    # 1) results of earlier signals
    for msg in tracker.update_open(state, candles, now):
        telegram.send(msg)

    # 2) sentiment
    crypto_syms = [a.symbol.split("/")[0] for a in assets if a.kind == "crypto"]
    sent = Sentiment().collect(crypto_syms)

    # 3) signals
    cands = []
    by_id = {a.id: a for a in assets}
    for aid, df in candles.items():
        for sig in analyze(df, state.get("trigger_stats")):
            sig = apply_sentiment(sig, by_id[aid], sent)
            sig["grade"] = grade(sig["score"])
            cands.append((by_id[aid], sig))
    log.info("%d candidate setups", len(cands))
    for a, s in sorted(cands, key=lambda c: c[1]["score"], reverse=True)[:10]:
        log.info("  %-28s %-5s %5.1f %s", a.symbol, s["direction"], s["score"], s["grade"])

    last = state.get("last_batch")
    batch_due = not last or now - datetime.fromisoformat(last) >= timedelta(minutes=config.BATCH_EVERY_MIN - 1)
    picked, urgent = select(cands, state, now, batch_due)
    if batch_due:
        state["last_batch"] = now.isoformat()

    for asset, sig in picked:
        if telegram.send(telegram.format_signal(sig, asset, urgent=urgent)):
            state["signals"].append({
                "asset": asset.to_dict(), "symbol": asset.symbol, "direction": sig["direction"],
                "entry": sig["entry"], "sl": sig["sl"], "tp1": sig["tp1"], "tp2": sig["tp2"],
                "score": sig["score"], "grade": sig["grade"], "triggers": sig["triggers"],
                "candle_time": sig["candle_time"], "sent_at": now.isoformat(), "status": "open",
            })
    log.info("sent %d signal(s)%s", len(picked), " (urgent)" if urgent else "")

    # 4) daily report
    text, report = tracker.daily_report(state, now)
    if text:
        telegram.send(text)
        day, md = report
        os.makedirs(config.REPORTS_DIR, exist_ok=True)
        with open(os.path.join(config.REPORTS_DIR, f"{day}.md"), "w") as f:
            f.write(md)

    tracker.save_state(state)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true", help="send a Telegram test message")
    args = p.parse_args()
    if args.test:
        ok = telegram.send("✅ Signal bot is connected. Signals will arrive here.")
        sys.exit(0 if ok else 1)
    run()


if __name__ == "__main__":
    main()
