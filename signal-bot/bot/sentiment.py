"""News & social sentiment for crypto and metals (no API keys needed).

Sources:
  * Crypto news RSS: CoinDesk, Cointelegraph, Decrypt, CryptoSlate, Bitcoin Magazine, The Block
  * Google News RSS searches (per metal and for the biggest coins)
  * Reddit RSS: r/CryptoCurrency, r/CryptoMarkets, r/Bitcoin, r/Gold, r/Silverbugs
  * Crypto Fear & Greed Index (alternative.me)
  * CoinGecko trending coins

Headlines are scored with VADER plus a trading vocabulary (surge, crash,
hack, ETF approval ...), weighted by how recent they are, and mapped to
each coin/metal they mention.
"""
import calendar
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from . import config
from .http import get

log = logging.getLogger(__name__)

CRYPTO_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://cryptoslate.com/feed/",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.theblock.co/rss.xml",
    "https://www.reddit.com/r/CryptoCurrency/hot/.rss",
    "https://www.reddit.com/r/CryptoMarkets/hot/.rss",
    "https://www.reddit.com/r/Bitcoin/hot/.rss",
]
METAL_FEEDS = {
    "GOLD": ["https://www.reddit.com/r/Gold/hot/.rss"],
    "SILVER": ["https://www.reddit.com/r/Silverbugs/hot/.rss"],
}


def google_news(query):
    return ("https://news.google.com/rss/search?q=" + quote_plus(query + " when:1d")
            + "&hl=en-US&gl=US&ceid=US:en")


TRADING_LEXICON = {
    "surge": 2.5, "surges": 2.5, "surging": 2.5, "soar": 2.6, "soars": 2.6, "soaring": 2.6,
    "rally": 2.2, "rallies": 2.2, "rallying": 2.2, "bullish": 2.8, "bearish": -2.8,
    "plunge": -2.8, "plunges": -2.8, "plunging": -2.8, "crash": -3.0, "crashes": -3.0,
    "slump": -2.4, "slumps": -2.4, "tumble": -2.4, "tumbles": -2.4, "dump": -2.2, "dumps": -2.2,
    "hack": -3.0, "hacked": -3.0, "exploit": -2.8, "exploited": -2.8, "drained": -2.6,
    "liquidations": -1.4, "outflows": -1.6, "inflows": 1.6, "approval": 1.8, "approved": 1.8,
    "ath": 2.5, "record": 1.2, "breakout": 1.8, "breaks": 0.6, "selloff": -2.4, "sell-off": -2.4,
    "lawsuit": -2.0, "sued": -2.0, "ban": -2.2, "bans": -2.2, "delist": -2.6, "delisting": -2.6,
    "adoption": 1.6, "upgrade": 1.2, "partnership": 1.4, "rebound": 1.8, "rebounds": 1.8,
    "recover": 1.5, "recovers": 1.5, "slides": -1.8, "slips": -1.4, "gains": 1.6, "gain": 1.2,
    "jumps": 2.0, "jump": 1.8, "falls": -1.6, "fall": -1.4, "drops": -1.6, "drop": -1.4,
    "rises": 1.5, "rise": 1.3, "climbs": 1.6, "higher": 0.8, "lower": -0.8, "losses": -1.5,
    "bull": 1.5, "bear": -1.5, "rejected": -1.8, "rejection": -1.6, "fud": -1.5,
    "accumulation": 1.4, "accumulate": 1.2, "demand": 0.8, "shortage": 1.0, "deficit": 0.6,
    "weakens": -1.2, "strengthens": 1.2, "pressure": -0.6, "fears": -1.4, "uncertainty": -0.8,
}

# coin -> words that identify it in a headline (lowercase). Symbols in
# CAPITALS are also matched as whole words, so most coins work automatically.
COIN_NAMES = {
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "SOL": ["solana"], "XRP": ["ripple"],
    "DOGE": ["dogecoin"], "ADA": ["cardano"], "AVAX": ["avalanche"], "DOT": ["polkadot"],
    "LINK": ["chainlink"], "LTC": ["litecoin"], "BCH": ["bitcoin cash"], "TRX": ["tron"],
    "SHIB": ["shiba inu"], "PEPE": ["pepe"], "SUI": ["sui network"], "TON": ["toncoin"],
    "NEAR": ["near protocol"], "APT": ["aptos"], "ARB": ["arbitrum"], "OP": ["optimism"],
    "UNI": ["uniswap"], "AAVE": ["aave"], "ATOM": ["cosmos"], "XLM": ["stellar"],
    "HBAR": ["hedera"], "FIL": ["filecoin"], "INJ": ["injective"], "RENDER": ["render"],
    "FET": ["fetch.ai"], "TAO": ["bittensor"], "WIF": ["dogwifhat"], "BONK": ["bonk"],
    "XMR": ["monero"], "ETC": ["ethereum classic"], "ALGO": ["algorand"], "SEI": ["sei network"],
    "TIA": ["celestia"], "JUP": ["jupiter"], "ONDO": ["ondo"], "ENA": ["ethena"],
    "HYPE": ["hyperliquid"], "TRUMP": ["trump coin", "$trump"], "POL": ["polygon"],
    "KAS": ["kaspa"], "ICP": ["internet computer"], "MKR": ["maker"], "LDO": ["lido"],
}
METAL_NAMES = {
    "GOLD": ["gold", "bullion", "xau"], "SILVER": ["silver", "xag"], "COPPER": ["copper"],
    "PLATINUM": ["platinum"], "PALLADIUM": ["palladium"],
}


def _clean(text):
    return re.sub(r"<[^>]+>", " ", text or "").replace("&nbsp;", " ").strip()


class Sentiment:
    def __init__(self):
        self.vader = SentimentIntensityAnalyzer()
        self.vader.lexicon.update(TRADING_LEXICON)
        self.scores = {}       # key -> list of (compound, weight, title)
        self.fear_greed = None
        self.trending = set()
        self.sources_ok = 0
        self.sources_failed = 0

    # ------------------------------------------------------------ collect
    def collect(self, crypto_symbols):
        jobs = [(u, "crypto", None) for u in CRYPTO_FEEDS]
        jobs.append((google_news("crypto market"), "crypto", None))
        for metal in METAL_NAMES:
            jobs.append((google_news(f"{metal.lower()} price"), "metal", metal))
            for u in METAL_FEEDS.get(metal, []):
                jobs.append((u, "metal", metal))
        for sym in crypto_symbols[:15]:
            name = COIN_NAMES.get(sym, [sym])[0]
            jobs.append((google_news(f"{name} crypto"), "crypto", sym))

        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda j: self._fetch(j[0]), jobs))
        seen = set()
        for (url, group, tag), entries in zip(jobs, results):
            for title, text, age_h in entries:
                key = title.lower()[:90]
                if key in seen:
                    continue
                seen.add(key)
                self._score(title, text, age_h, group, tag, crypto_symbols)

        self._fear_greed()
        self._trending()
        log.info("sentiment: %d sources ok, %d failed, %d keys",
                 self.sources_ok, self.sources_failed, len(self.scores))
        return self

    def _fetch(self, url):
        try:
            raw = get(url, as_json=False, timeout=12, retries=1)
            feed = feedparser.parse(raw)
            now = time.time()
            out = []
            for e in feed.entries[:60]:
                ts = e.get("published_parsed") or e.get("updated_parsed")
                age_h = (now - calendar.timegm(ts)) / 3600 if ts else 6.0
                if age_h > 36:
                    continue
                out.append((_clean(e.get("title")), _clean(e.get("summary"))[:400], max(0.0, age_h)))
            self.sources_ok += 1
            return out
        except Exception as e:
            self.sources_failed += 1
            log.debug("feed failed %s: %s", url, e)
            return []

    def _score(self, title, text, age_h, group, tag, crypto_symbols):
        if not title:
            return
        compound = self.vader.polarity_scores(title)["compound"]
        if text:
            compound = 0.7 * compound + 0.3 * self.vader.polarity_scores(text)["compound"]
        weight = math.pow(0.5, age_h / 8)  # half-life 8 hours
        full = f"{title} {text}"
        low = full.lower()

        keys = set()
        if tag:
            keys.add(tag)
        if group == "crypto":
            keys.add("CRYPTO")
            for sym in crypto_symbols:
                names = COIN_NAMES.get(sym, [])
                if (len(sym) >= 3 and re.search(rf"\b{re.escape(sym)}\b", full)) or \
                        any(re.search(rf"\b{re.escape(n)}\b", low) for n in names):
                    keys.add(sym)
        if group == "metal":  # crypto articles mentioning gold are usually comparisons
            for metal, names in METAL_NAMES.items():
                if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names):
                    keys.add(metal)
        for k in keys:
            self.scores.setdefault(k, []).append((compound, weight, title))

    def _fear_greed(self):
        try:
            self.fear_greed = int(get("https://api.alternative.me/fng/?limit=1")["data"][0]["value"])
        except Exception as e:
            log.debug("fear&greed failed: %s", e)

    def _trending(self):
        try:
            data = get("https://api.coingecko.com/api/v3/search/trending")
            self.trending = {c["item"]["symbol"].upper() for c in data.get("coins", [])}
        except Exception as e:
            log.debug("coingecko trending failed: %s", e)

    # ------------------------------------------------------------ query
    def _avg(self, key):
        items = self.scores.get(key, [])
        w = sum(i[1] for i in items)
        if not items or w == 0:
            return 0.0, 0, None
        avg = sum(i[0] * i[1] for i in items) / w
        top = max(items, key=lambda i: abs(i[0]) * i[1])[2]
        return avg, len(items), top

    def score_for(self, asset):
        own, n, top = self._avg(asset.sentiment_key)
        if asset.kind == "crypto":
            market, mn, _ = self._avg("CRYPTO")
            # few headlines about the coin itself -> lean on the market mood
            w = min(1.0, n / 5)
            score = w * own + (1 - w) * market
            n_total = n + (mn if n < 5 else 0)
        else:
            score, n_total = own, n
        # shrink when evidence is thin, clamp to [-1, 1]
        score *= min(1.0, n_total / 4) if n_total else 0
        return {"score": max(-1.0, min(1.0, score * 2)), "n": n_total, "top": top}
