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
from .strategy import analyze

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
    "min_score": [70, 75, 80, 85, 90],
    "btc_align": [False, True],       # alts only in the direction of BTC's 4H trend
    "sl_mult": [1.2, 1.8],
    "tp_r": [1.0, 1.5],
}
FEE_PCT = 0.10  # round-trip trading cost (fees + spread), in % of price


def btc_regime(btc):
    """+1 / -1 per 15m bar: BTC 4H EMA21 rising or falling (completed 4H bars only)."""
    from .indicators import ema, resample
    e = ema(resample(btc, "4h")["close"], 21)
    reg = (e > e.shift(2)).map({True: 1, False: -1})
    reg.index = reg.index + pd.Timedelta(hours=4)  # known only once the 4H bar closes
    return reg.reindex(btc.index, method="ffill").fillna(0)


def run_variants(data, regime=None):
    rows = []
    for ms, ba, slm, tpr in itertools.product(*VARIANTS.values()):
        trades = []
        for sym, (df, setups) in data.items():
            busy_until = -1
            for i, sig in setups:
                if i <= busy_until or sig["score"] < ms:
                    continue
                if ba and regime is not None and not sym.startswith(("Gold", "Silver", "Copper")) \
                        and not sym.startswith("BTC/"):
                    want = 1 if sig["direction"] == "LONG" else -1
                    if regime.asof(df.index[i]) != want:
                        continue
                r, exit_i, risk = simulate(df, i, sig, slm, tpr)
                r -= FEE_PCT / 100 * sig["entry"] / risk  # costs, expressed in R
                busy_until = exit_i
                trades.append((sym, sig["direction"], r, tuple(sig["triggers"])))
        if not trades:
            continue
        rs = pd.Series([t[2] for t in trades])
        rows.append({"min_score": ms, "btc_align": ba, "sl_atr": slm, "tp_R": tpr,
                     "trades": len(trades), "per_day": round(len(trades) / DAYS, 1),
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
    rows = run_variants(data, regime)
    cols = ["min_score", "btc_align", "sl_atr", "tp_R", "trades", "per_day", "win%", "avg_R", "total_R"]
    print("\n=== VARIANTS (best average R first) ===")
    print(pd.DataFrame(rows)[cols].to_string(index=False))
    current = next(r for r in rows if r["min_score"] == 80 and not r["btc_align"]
                   and r["sl_atr"] == 1.2 and r["tp_R"] == 1.5)
    best = rows[0]
    for name, r in (("SCORE>=80 BASELINE", current), ("BEST VARIANT", best)):
        print(f"\n=== {name}: {r['trades']} trades, win {r['win%']}%, avg {r['avg_R']}R ===")
        print(breakdown(r["_trades"], "sym").to_string())
        print(breakdown(r["_trades"], "dir").to_string())
        print(breakdown(r["_trades"], "trig").to_string())


if __name__ == "__main__":
    main()
