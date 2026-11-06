"""Replay real Kraken history through the strategy and measure what wins.

    python -m bot.backtest            # fetches ~30 days of 15m candles (needs internet)

For every 15-minute bar of every market it asks the strategy for setups
(technical only: old news isn't available), then simulates the trade:
exit at TP or SL (stop checked first in the same candle = conservative),
or at the close after SIGNAL_EXPIRY_HOURS. One open trade per market at a time.
Results are printed for several rule variants so we can pick the best.
"""
import itertools
import logging
import time

import pandas as pd

from . import config, markets
from .strategy import analyze, btc_regime

log = logging.getLogger("backtest")

DAYS = 30
WINDOW = 700  # bars passed to the strategy at each step


def history(asset, days=DAYS):
    """15m candles. Kraken Futures perps give ~30 days; spot only gives 7.5."""
    if asset.source == "kraken_futures":
        now = int(time.time())
        parts, start = [], now - days * 86400
        while start < now:
            end = min(start + 5 * 86400, now)
            d = markets.get(f"{markets.KRAKEN_FUT}/api/charts/v1/trade/{asset.ref}/15m",
                            params={"from": start, "to": end})
            parts += [[c["time"] / 1000, c["open"], c["high"], c["low"], c["close"], c.get("volume", 0)]
                      for c in d.get("candles", [])]
            start = end
            time.sleep(0.2)
        return markets._frame(parts)
    return markets.candles_15m(asset)


def collect(df):
    """All raw setups along the history: list of (bar index, signal)."""
    out = []
    for i in range(300, len(df) - 1):
        win = df.iloc[max(0, i - WINDOW + 1): i + 1]
        for sig in analyze(win):
            out.append((i, sig))
    return out


def simulate(df, i, sig, sl_mult, tp_r):
    d = 1 if sig["direction"] == "LONG" else -1
    f = sig["feat"]
    risk = min(max(sl_mult * f["atr1h"], f["swing_dist"]), max(config.SL_ATR_MAX, sl_mult) * f["atr1h"])
    e = sig["entry"]
    sl, tp = e - d * risk, e + d * risk * tp_r
    last = min(len(df) - 1, i + config.SIGNAL_EXPIRY_HOURS * 4)
    for j in range(i + 1, last + 1):
        hi, lo = df["high"].iat[j], df["low"].iat[j]
        if (lo if d == 1 else hi) * d <= sl * d:
            return -1.0, j, risk
        if (hi if d == 1 else lo) * d >= tp * d:
            return tp_r, j, risk
    return (df["close"].iat[last] - e) * d / risk, last, risk


VARIANTS = {
    "entry": ["any", "pullback", "not_extended"],
    "sl_mult": [1.2, 2.0, 3.0],
    "tp_r": [1.0, 1.5],
    "max_same_dir": [99, 2],          # max open crypto trades in the same direction
}
MIN_SCORE = 85          # live rule
FEE_PCT = 0.10  # round-trip trading cost (fees + spread), in % of price


def entry_ok(sig, style):
    if style == "pullback":
        return bool({"trend_pullback", "rsi_pullback"} & set(sig["triggers"]))
    if style == "not_extended":
        return sig["feat"].get("ext", 0) < 1.0
    return True


def run_variants(data, regime=None, since=None):
    """Simulate every variant chronologically across all markets (so the
    same-direction cap works like the live bot)."""
    events = []
    for sym, (df, setups) in data.items():
        is_metal = sym.startswith(("Gold", "Silver", "Copper"))
        for i, sig in setups:
            if sig["score"] < MIN_SCORE:
                continue
            if since is not None and df.index[i] < since:
                continue
            if regime is not None and not is_metal and not sym.startswith("BTC/"):
                if regime.asof(df.index[i]) != (1 if sig["direction"] == "LONG" else -1):
                    continue
            events.append((df.index[i], sym, i, sig, is_metal))
    events.sort(key=lambda e: e[0])

    rows = []
    for style, slm, tpr, cap in itertools.product(*VARIANTS.values()):
        busy_until = {}          # sym -> exit time
        open_trades = []         # (exit time, direction) for crypto
        trades = []
        for t, sym, i, sig, is_metal in events:
            if not entry_ok(sig, style):
                continue
            if busy_until.get(sym) is not None and t <= busy_until[sym]:
                continue
            open_trades = [o for o in open_trades if o[0] > t]
            if not is_metal and sum(1 for o in open_trades if o[1] == sig["direction"]) >= cap:
                continue
            df = data[sym][0]
            r, exit_i, risk = simulate(df, i, sig, slm, tpr)
            r -= FEE_PCT / 100 * sig["entry"] / risk
            busy_until[sym] = df.index[exit_i]
            if not is_metal:
                open_trades.append((df.index[exit_i], sig["direction"]))
            trades.append((sym, sig["direction"], r, tuple(sig["triggers"])))
        if not trades:
            continue
        rs = pd.Series([x[2] for x in trades])
        days = DAYS if since is None else max(1, (events[-1][0] - since).days or 1)
        rows.append({"entry": style, "sl_atr": slm, "tp_R": tpr, "max_same_dir": cap,
                     "trades": len(trades), "per_day": round(len(trades) / days, 1),
                     "win%": round((rs > 0).mean() * 100, 1), "avg_R": round(rs.mean(), 3),
                     "total_R": round(rs.sum(), 1), "_trades": trades})
    return sorted(rows, key=lambda r: r["avg_R"], reverse=True)


def breakdown(trades, key):
    df = pd.DataFrame(trades, columns=["sym", "dir", "r", "trig"])
    if key == "trig":
        df = df.explode("trig")
    g = df.groupby(key)["r"].agg(n="count", win=lambda s: round((s > 0).mean() * 100), avg_R="mean")
    return g.sort_values("avg_R", ascending=False).round(3)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    assets = []
    try:
        assets += markets.crypto_universe()
    except Exception as e:
        log.error("crypto universe failed: %s", e)
    tick = markets.futures_tickers()
    perps = {t["symbol"].upper() for t in tick}
    assets += markets.metal_universe(tick)
    assets = [a for a in assets if a.kind == "metal" and a.source == "kraken_futures"
              or a.kind == "crypto"]  # crypto_universe already applies the allowlist
    # use the perp (30 days of history) for crypto when Kraken has one
    for a in assets:
        if a.kind == "crypto":
            base = "XBT" if a.symbol.startswith("BTC/") else a.symbol.split("/")[0]
            if f"PF_{base}USD" in perps:
                a.source, a.ref = "kraken_futures", f"PF_{base}USD"

    data = {}
    for a in assets:
        try:
            df = history(a)
            t0 = time.time()
            data[a.symbol] = (df, collect(df))
            log.info("%-26s %5d bars %4d raw setups (%.0fs)", a.symbol, len(df), len(data[a.symbol][1]),
                     time.time() - t0)
        except Exception as e:
            log.warning("skip %s: %s", a.symbol, e)

    btc = data.get("BTC/USD")
    regime = btc_regime(btc[0]) if btc else None
    cols = ["entry", "sl_atr", "tp_R", "max_same_dir", "trades", "per_day", "win%", "avg_R", "total_R"]
    for label, since in (("LAST 30 DAYS", None),
                         ("LAST 3 DAYS", pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3))):
        rows = run_variants(data, regime, since)
        print(f"\n=== {label}: score>={MIN_SCORE}, alts follow BTC 4H (best average R first) ===")
        print(pd.DataFrame(rows)[cols].to_string(index=False))
        live = next(r for r in rows if r["entry"] == "any" and r["sl_atr"] == 1.2
                    and r["tp_R"] == 1.5 and r["max_same_dir"] == 99)
        print(f"current live rules: {live['trades']} trades, win {live['win%']}%, avg {live['avg_R']}R")
        print(breakdown(live["_trades"], "dir").to_string())


if __name__ == "__main__":
    main()
