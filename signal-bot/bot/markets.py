"""Market discovery and candle data.

Everything here uses PUBLIC endpoints - no API keys needed:
  * Kraken Spot      https://api.kraken.com/0/public/...
  * Kraken Futures   https://futures.kraken.com/...   (perpetuals incl. metals if listed)
  * Yahoo Finance    COMEX metal futures, used only when Kraken has no market
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config
from .http import get

log = logging.getLogger(__name__)

KRAKEN = "https://api.kraken.com/0/public"
KRAKEN_FUT = "https://futures.kraken.com"
YAHOO_HOSTS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/",
    "https://query2.finance.yahoo.com/v8/finance/chart/",
]

SYMBOL_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


@dataclass
class Asset:
    id: str                 # unique id, e.g. "kraken_spot:XXBTZUSD"
    symbol: str             # display, e.g. "BTC/USD"
    kind: str               # "crypto" or "metal"
    source: str             # "kraken_spot" | "kraken_futures" | "yahoo"
    ref: str                # identifier for that source
    sentiment_key: str      # e.g. "BTC" or "GOLD"
    venue: str              # where the user can trade it
    volume_usd: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return self.__dict__.copy()

    @staticmethod
    def from_dict(d):
        return Asset(**d)


def _kraken(path, params=None):
    data = get(f"{KRAKEN}/{path}", params=params)
    if data.get("error") and not data.get("result"):
        raise RuntimeError(f"Kraken error {data['error']}")
    return data["result"]


# ---------------------------------------------------------------- universe
def crypto_universe():
    pairs = _kraken("AssetPairs")
    usd = {}
    for key, p in pairs.items():
        ws = p.get("wsname", "")
        if not ws.endswith("/USD") or key.endswith(".d"):
            continue
        if p.get("status", "online") != "online":
            continue
        base = ws.split("/")[0]
        base = SYMBOL_ALIASES.get(base, base)
        if base in config.STABLECOINS or base in config.FIAT:
            continue
        usd[key] = base

    volumes = {}
    keys = list(usd)
    for i in range(0, len(keys), 40):
        chunk = keys[i:i + 40]
        try:
            res = _kraken("Ticker", {"pair": ",".join(chunk)})
        except Exception as e:  # one bad chunk shouldn't kill the scan
            log.warning("ticker chunk failed: %s", e)
            continue
        for k, t in res.items():
            try:
                volumes[k] = float(t["v"][1]) * float(t["p"][1])
            except (KeyError, ValueError, IndexError):
                pass
        time.sleep(0.3)

    assets = []
    for key, base in usd.items():
        vol = volumes.get(key, 0.0)
        is_gold = base in config.GOLD_TOKENS
        assets.append(Asset(
            id=f"kraken_spot:{key}",
            symbol=f"{base}/USD",
            kind="metal" if is_gold else "crypto",
            source="kraken_spot",
            ref=key,
            sentiment_key="GOLD" if is_gold else base,
            venue="Kraken Spot",
            volume_usd=vol,
        ))

    gold = [a for a in assets if a.kind == "metal" and a.volume_usd > 0]
    crypto = sorted(
        (a for a in assets if a.kind == "crypto" and a.volume_usd >= config.MIN_USD_VOLUME_24H),
        key=lambda a: a.volume_usd, reverse=True,
    )[: config.TOP_CRYPTO]
    return crypto + gold


def futures_tickers():
    try:
        return get(f"{KRAKEN_FUT}/derivatives/api/v3/tickers").get("tickers", [])
    except Exception as e:
        log.warning("kraken futures tickers failed: %s", e)
        return []


def metal_universe(tickers=None):
    tickers = tickers if tickers is not None else futures_tickers()
    perps = [t for t in tickers if str(t.get("symbol", "")).upper().startswith("PF_")]
    assets = []
    for m in config.METALS:
        match = next(
            (t for t in perps if m["code"] in t["symbol"].upper() and t["symbol"].upper().endswith("USD")),
            None,
        )
        if match:
            assets.append(Asset(
                id=f"kraken_futures:{match['symbol']}",
                symbol=f"{m['name']} ({m['code']}/USD perp)",
                kind="metal",
                source="kraken_futures",
                ref=match["symbol"],
                sentiment_key=m["name"].upper(),
                venue="Kraken Pro Futures",
                volume_usd=float(match.get("volumeQuote") or 0),
            ))
        else:
            assets.append(Asset(
                id=f"yahoo:{m['yahoo']}",
                symbol=f"{m['name']} ({m['code']}/USD)",
                kind="metal",
                source="yahoo",
                ref=m["yahoo"],
                sentiment_key=m["name"].upper(),
                venue="Not found on Kraken - reference: COMEX futures (use the % levels)",
            ))
    return assets


# ---------------------------------------------------------------- candles
def _frame(rows):
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time").astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # drop the candle that is still forming
    now = datetime.now(timezone.utc)
    if len(df) and df.index[-1] + timedelta(minutes=15) > now:
        df = df.iloc[:-1]
    return df


def candles_15m(asset):
    if asset.source == "kraken_spot":
        res = _kraken("OHLC", {"pair": asset.ref, "interval": 15})
        rows = next(v for k, v in res.items() if k != "last")
        return _frame([[r[0], r[1], r[2], r[3], r[4], r[6]] for r in rows])

    if asset.source == "kraken_futures":
        now = int(time.time())
        data = get(
            f"{KRAKEN_FUT}/api/charts/v1/trade/{asset.ref}/15m",
            params={"from": now - 8 * 86400, "to": now},
        )
        rows = [
            [c["time"] / 1000, c["open"], c["high"], c["low"], c["close"], c.get("volume", 0)]
            for c in data.get("candles", [])
        ]
        return _frame(rows)

    if asset.source == "yahoo":
        data, err = None, None
        for host in YAHOO_HOSTS:  # Yahoo rate-limits shared IPs; try the mirror too
            try:
                data = get(host + asset.ref, params={"interval": "15m", "range": "10d"}, retries=1)
                break
            except Exception as e:
                err = e
                time.sleep(2)
        if data is None:
            raise err
        r = data["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        rows = [
            [t, o, h, l, c, v or 0]
            for t, o, h, l, c, v in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"])
            if None not in (o, h, l, c)
        ]
        return _frame(rows)

    raise ValueError(f"unknown source {asset.source}")
