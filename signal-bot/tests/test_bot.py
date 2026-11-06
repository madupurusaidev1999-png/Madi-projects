"""Offline tests: synthetic candles, mocked network."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from bot import config, main, markets, strategy, tracker
from bot.markets import Asset
from bot.sentiment import Sentiment


def make_candles(trend=0.0015, n=700, seed=1, end=None, kick=0.0):
    rng = np.random.default_rng(seed)
    end = end or datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=16)
    idx = pd.date_range(end=end, periods=n, freq="15min", tz="UTC")
    rets = trend + rng.normal(0, 0.004, n)
    rets[-3:] += kick
    close = 100 * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.002, n)) * close
    vol = rng.uniform(80, 120, n)
    vol[-1] *= 2
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread, "close": close, "volume": vol}, index=idx)


def find_signal(direction, trend):
    for seed in range(200):
        for kick in (0.0, 0.006 if direction == "LONG" else -0.006):
            sigs = [s for s in strategy.analyze(make_candles(trend, seed=seed, kick=kick)) if s["direction"] == direction]
            if sigs:
                return sigs[0]
    pytest.fail(f"no {direction} signal produced")


def test_long_signal_levels():
    s = find_signal("LONG", 0.0008)
    assert s["sl"] < s["entry"] < s["tp1"] < s["tp2"]
    assert abs((s["tp1"] - s["entry"]) / (s["entry"] - s["sl"]) - config.TP1_R) < 1e-9
    assert 0 <= s["score"] <= 100 and s["triggers"]


def test_short_signal_levels():
    s = find_signal("SHORT", -0.0008)
    assert s["tp2"] < s["tp1"] < s["entry"] < s["sl"]


def test_never_long_in_downtrend():
    for seed in range(30):
        assert not [s for s in strategy.analyze(make_candles(-0.002, seed=seed)) if s["direction"] == "LONG"]


def test_short_history_ignored():
    assert strategy.analyze(make_candles(n=100)) == []


def test_sentiment_scoring():
    s = Sentiment()
    s._score("Bitcoin price surges to record high as ETF inflows soar", "", 1, "crypto", None, ["BTC", "ETH"])
    s._score("Ethereum plunges after major exchange hack", "", 1, "crypto", None, ["BTC", "ETH"])
    s._score("Gold slides as dollar strengthens", "", 2, "metal", "GOLD", [])
    btc, eth, gold = (s._avg(k)[0] for k in ("BTC", "ETH", "GOLD"))
    assert btc > 0.3 and eth < -0.3 and gold < 0
    a = Asset("x", "BTC/USD", "crypto", "kraken_spot", "x", "BTC", "Kraken")
    assert -1 <= s.score_for(a)["score"] <= 1


def test_tracker_tp_and_sl():
    df = make_candles(0.0)
    t0 = df.index[-20]
    entry = float(df["close"].iloc[-20])
    asset = {"id": "a"}

    def rec(direction, sl, tp1, tp2):
        return {"asset": asset, "symbol": "T/USD", "direction": direction, "entry": entry,
                "sl": sl, "tp1": tp1, "tp2": tp2, "triggers": ["ema_cross"], "grade": "B",
                "score": 60, "candle_time": t0.isoformat(),
                "sent_at": (t0 + timedelta(minutes=15)).isoformat(), "status": "open"}

    state = {"signals": [rec("LONG", entry * 0.5, entry * 1.0001, entry * 9),
                         rec("SHORT", entry * 1.0001, entry * 0.5, entry * 0.1)],
             "trigger_stats": {}}
    msgs = tracker.update_open(state, {"a": df})
    statuses = {s["direction"]: s["status"] for s in state["signals"]}
    assert statuses["SHORT"] == "sl"
    assert statuses["LONG"] in ("tp1", "tp1_be")
    assert state["trigger_stats"]["ema_cross"] == {"w": 1, "l": 1}
    assert len(msgs) == 2


def test_trigger_weight_learns():
    assert strategy.trigger_weight("x", {}) == 1.0
    assert strategy.trigger_weight("x", {"x": {"w": 8, "l": 4}}) > 1.0
    assert strategy.trigger_weight("x", {"x": {"w": 2, "l": 10}}) < 1.0


def test_end_to_end_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tracker, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    assets = [Asset(f"kraken_spot:{i}", f"C{i}/USD", "crypto", "kraken_spot", str(i), f"C{i}", "Kraken Spot")
              for i in range(12)]
    gold = [Asset("kraken_futures:PF_XAUTUSD", "Gold perp", "metal", "kraken_futures", "PF_XAUTUSD", "GOLD", "Kraken"),
            Asset("kraken_spot:PAXGUSD", "PAXG/USD", "metal", "kraken_spot", "PAXGUSD", "GOLD", "Kraken")]
    monkeypatch.setattr(markets, "crypto_universe", lambda: assets)
    monkeypatch.setattr(markets, "metal_universe", lambda: gold)
    data = {a.id: make_candles(0.0008 * (1 if i % 2 else -1), seed=i, kick=0.006 * (1 if i % 2 else -1))
            for i, a in enumerate(assets)}
    data.update({g.id: make_candles(0.0008, seed=1, kick=0.006) for g in gold})  # identical gold setups
    monkeypatch.setattr(config, "MAX_PER_BATCH", 20)
    monkeypatch.setattr(config, "MAX_PER_HOUR", 20)
    monkeypatch.setattr(markets, "candles_15m", lambda a: data[a.id])
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(Sentiment, "_fetch", lambda self, url: [])
    monkeypatch.setattr(Sentiment, "_fear_greed", lambda self: None)
    monkeypatch.setattr(Sentiment, "_trending", lambda self: None)

    main.run()
    st = tracker.load_state()
    assert st["last_batch"] and 1 <= len(st["signals"]) <= config.MAX_PER_BATCH
    out = capsys.readouterr().out
    assert "Entry:" in out and "TP1:" in out and "Enter before" in out and "Close by" in out
    assert sum(1 for s in st["signals"] if s["asset"]["sentiment_key"] == "GOLD") <= 1

    # an immediate second run is not a batch: only urgent signals, and no repeats
    n = len(st["signals"])
    main.run()
    st2 = tracker.load_state()
    ids = [(s["asset"]["id"], s["direction"]) for s in st2["signals"]]
    assert len(ids) == len(set(ids))
    assert all(s["score"] >= config.URGENT_SCORE for s in st2["signals"][n:])


def test_export_csv(tmp_path):
    import csv
    base = {"asset": {"id": "a"}, "symbol": "BTC/USD", "name": "BTC/USD", "grade": "A", "score": 91,
            "entry": 100.0, "sl": 98.0, "tp1": 103.0, "tp2": 105.0, "triggers": []}
    state = {"signals": [
        dict(base, direction="SHORT", sent_at="2026-11-05T10:00:00+00:00", status="sl", result_r=-1.0,
             closed_at="2026-11-05T11:00:00+00:00"),
        dict(base, direction="LONG", sent_at="2026-11-05T12:00:00+00:00", status="tp2", result_r=2.5, shift=5.0),
        dict(base, direction="LONG", sent_at="2026-11-05T13:00:00+00:00", status="open"),
    ]}
    path = tmp_path / "signals.csv"
    tracker.export_csv(state, str(path))
    rows = list(csv.DictReader(open(path)))
    assert [r["result"] for r in rows] == ["OPEN", "WON (TP2)", "LOST (stop-loss)"]
    assert rows[1]["entry"] == "105" and rows[2]["side"] == "SELL" and rows[2]["result_R"] == "-1.0"
