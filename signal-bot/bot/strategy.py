"""Intraday signal logic: 4H bias -> 1H trend -> 15m entry trigger.

Every candidate gets a 0-100 score. News/social sentiment then adds or
removes up to config.SENTIMENT_MAX_POINTS. Trades are meant to last a few
hours up to one day.
"""
from . import config
import pandas as pd

from .indicators import adx, atr, ema, macd_hist, resample, rsi

TRIGGERS = ("ema_cross", "macd_flip", "rsi_pullback", "breakout", "trend_pullback")


def _crossed(a, b, bars, up=True):
    """True if series a crossed b within the last `bars` candles."""
    diff = (a - b).iloc[-(bars + 1):]
    signs = diff > 0 if up else diff < 0
    return bool(signs.iloc[-1]) and not bool(signs.all())


def trigger_weight(name, stats):
    """Learning from results: triggers that win more get more weight."""
    s = (stats or {}).get(name, {})
    n = s.get("w", 0) + s.get("l", 0)
    if n < 10:
        return 1.0
    winrate = s["w"] / n
    # break-even win rate at TP1=1.5R is 40%
    return max(0.6, min(1.4, winrate / 0.4))


def analyze(df15, trigger_stats=None):
    """Return a list of 0-2 candidate signals (long and/or short)."""
    if df15 is None or len(df15) < config.MIN_CANDLES_15M:
        return []
    h1 = resample(df15, "1h")
    h4 = resample(df15, "4h")
    if len(h1) < 60 or len(h4) < 20:
        return []

    c15 = df15["close"]
    e9, e21 = ema(c15, 9), ema(c15, 21)
    r15 = rsi(c15)
    mh = macd_hist(c15)
    atr15 = atr(df15).iloc[-1]
    vol = df15["volume"]
    vol_avg = vol.iloc[-21:-1].mean()
    vol_ratio = float(vol.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0

    c1 = h1["close"]
    e20h, e50h = ema(c1, 20), ema(c1, 50)
    adx1h = float(adx(h1).iloc[-1])
    atr1h = float(atr(h1).iloc[-1])
    e21_4h = ema(h4["close"], 21)

    price = float(c15.iloc[-1])
    if atr1h <= 0 or price <= 0:
        return []

    out = []
    for direction in (1, -1):
        up = direction == 1
        trend_ok = (e20h.iloc[-1] > e50h.iloc[-1] and c1.iloc[-1] > e50h.iloc[-1]) if up else \
                   (e20h.iloc[-1] < e50h.iloc[-1] and c1.iloc[-1] < e50h.iloc[-1])
        counter = (e20h.iloc[-1] < e50h.iloc[-1] and c1.iloc[-1] < e50h.iloc[-1]) if up else \
                  (e20h.iloc[-1] > e50h.iloc[-1] and c1.iloc[-1] > e50h.iloc[-1])
        if counter:
            continue  # never trade against the 1H trend

        bias_ok = (e21_4h.iloc[-1] > e21_4h.iloc[-3]) if up else (e21_4h.iloc[-1] < e21_4h.iloc[-3])

        fired = []
        if _crossed(e9, e21, 4, up):
            fired.append("ema_cross")
        if _crossed(mh, mh * 0, 4, up):
            fired.append("macd_flip")
        # trend pullback: price dipped to the 15m EMA21 in the last hour and bounced
        if trend_ok:
            e21_now = e21.iloc[-1]
            if up and df15["low"].iloc[-4:].min() <= e21_now * 1.001 and price > e9.iloc[-1] > e21_now:
                fired.append("trend_pullback")
            if not up and df15["high"].iloc[-4:].max() >= e21_now * 0.999 and price < e9.iloc[-1] < e21_now:
                fired.append("trend_pullback")
        rmin, rmax = r15.iloc[-7:-1].min(), r15.iloc[-7:-1].max()
        if up and rmin < 42 and r15.iloc[-1] > 45 and r15.iloc[-1] > r15.iloc[-2]:
            fired.append("rsi_pullback")
        if not up and rmax > 58 and r15.iloc[-1] < 55 and r15.iloc[-1] < r15.iloc[-2]:
            fired.append("rsi_pullback")
        hi20, lo20 = df15["high"].iloc[-21:-1].max(), df15["low"].iloc[-21:-1].min()
        if (up and price > hi20) or (not up and price < lo20):
            fired.append("breakout")
        if not fired:
            continue

        score = 0.0
        reasons = []
        if trend_ok:
            score += 25
            reasons.append(f"1H {'up' if up else 'down'}trend (EMA20 {'>' if up else '<'} EMA50)")
        else:
            score += 8
            reasons.append("1H trend flat/turning")
        if bias_ok:
            score += 15
            reasons.append("4H trend agrees")
        if adx1h >= 30:
            score += 15
        elif adx1h >= 20:
            score += 10
        trig_pts = sum(12 * trigger_weight(t, trigger_stats) for t in fired)
        score += min(30, trig_pts)
        nice = {"ema_cross": f"15m EMA 9/21 cross {'up' if up else 'down'}",
                "macd_flip": f"15m MACD turned {'bullish' if up else 'bearish'}",
                "rsi_pullback": "15m RSI pullback entry",
                "trend_pullback": f"Pulled back to 15m EMA21 and {'bounced' if up else 'rejected'}",
                "breakout": f"15m breakout {'above' if up else 'below'} 5h range"}
        reasons.append(", ".join(nice[t] for t in fired))

        if vol_ratio >= 1.5:
            score += 10
        elif vol_ratio >= 1.0:
            score += 5
        rv = float(r15.iloc[-1])
        if (up and 45 <= rv <= 68) or (not up and 32 <= rv <= 55):
            score += 10
        if (up and rv > 75) or (not up and rv < 25):
            score -= 20
            reasons.append("RSI stretched - careful")
        if abs(price - e21.iloc[-1]) < 1.5 * atr15:
            score += 5  # not chasing an extended candle
        reasons.append(f"ADX {adx1h:.0f} · RSI {rv:.0f} · volume {vol_ratio:.1f}x avg")

        # levels: stop beyond the recent swing, bounded by 1H ATR
        swing = df15["low"].iloc[-8:].min() if up else df15["high"].iloc[-8:].max()
        swing_dist = abs(price - swing) + 0.2 * atr1h
        risk = min(max(config.SL_ATR_MULT * atr1h, swing_dist), config.SL_ATR_MAX * atr1h)
        out.append({
            "direction": "LONG" if up else "SHORT",
            "tech_score": round(score, 1),
            "score": round(score, 1),
            "entry": price,
            "sl": price - direction * risk,
            "tp1": price + direction * risk * config.TP1_R,
            "tp2": price + direction * risk * config.TP2_R,
            "risk": risk,
            "triggers": fired,
            "reasons": reasons,
            "candle_time": df15.index[-1].isoformat(),
            # raw features, used by the backtester and the quality filter
            "feat": {"trend_ok": bool(trend_ok), "bias_ok": bool(bias_ok), "adx": adx1h,
                     "rsi": rv, "vol_ratio": vol_ratio, "atr1h": atr1h, "swing_dist": swing_dist},
        })
    return out


def apply_sentiment(sig, asset, sent):
    """Blend news/social sentiment and crowd mood into the score."""
    d = 1 if sig["direction"] == "LONG" else -1
    s = sent.score_for(asset)
    pts = config.SENTIMENT_MAX_POINTS * s["score"] * d
    sig["score"] = round(sig["score"] + pts, 1)
    sig["sentiment"] = s
    if s["n"]:
        mood = "bullish" if s["score"] > 0.05 else "bearish" if s["score"] < -0.05 else "neutral"
        sig["reasons"].append(f"News sentiment {mood} ({s['score']:+.2f}, {s['n']} headlines)")

    if asset.kind == "crypto" and sent.fear_greed is not None:
        fg = sent.fear_greed
        if fg >= 80:      # extreme greed: longs crowded
            sig["score"] += -5 if d == 1 else 3
        elif fg <= 20:    # extreme fear: shorts crowded
            sig["score"] += 3 if d == 1 else -5
    if asset.kind == "crypto" and asset.symbol.split("/")[0] in sent.trending:
        sig["score"] += 4
        sig["reasons"].append("🔥 Trending on CoinGecko")
    sig["score"] = round(max(0.0, min(100.0, sig["score"])), 1)
    return sig


def grade(score):
    if score >= config.GRADE_A:
        return "A"
    if score >= config.GRADE_B:
        return "B"
    if score >= config.GRADE_C:
        return "C"
    return None


def btc_regime(btc):
    """+1 / -1 per 15m bar: BTC 4H EMA21 rising or falling (completed 4H bars only)."""
    e = ema(resample(btc, "4h")["close"], 21)
    reg = (e > e.shift(2)).map({True: 1, False: -1})
    reg.index = reg.index + pd.Timedelta(hours=4)  # known only once the 4H bar closes
    return reg.reindex(btc.index, method="ffill").fillna(0)
