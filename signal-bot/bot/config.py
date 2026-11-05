"""All tunable settings in one place. Change values here to adjust the bot."""
import os

# ---------- Telegram ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Timezone shown in messages, e.g. "Asia/Kolkata", "Europe/London", "UTC"
TIMEZONE = os.getenv("BOT_TIMEZONE", "UTC")

# ---------- Universe ----------
TOP_CRYPTO = 40                 # scan the N most-traded USD pairs on Kraken

# Only these coins are scanned (pairs offered on Kraken Prop / most prop firms).
# Edit this list to match exactly what your Kraken Prop account shows.
# Set BOT_ALLOW_ALL_CRYPTO=1 to scan every liquid Kraken pair instead.
CRYPTO_ALLOWLIST = {
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "BCH",
    "TRX", "XLM", "UNI", "AAVE", "NEAR", "SUI", "HBAR", "ATOM", "ETC", "FIL", "APT",
    "ARB", "OP", "INJ", "TON", "SHIB", "PEPE", "BNB",
}
ALLOW_ALL_CRYPTO = os.getenv("BOT_ALLOW_ALL_CRYPTO") == "1"
MIN_USD_VOLUME_24H = 1_000_000  # ignore illiquid coins below this 24h volume

STABLECOINS = {
    "USDT", "USDC", "DAI", "PYUSD", "USDG", "USDS", "USDE", "FDUSD", "TUSD", "USDD",
    "EURT", "EURC", "EURQ", "EURR", "USDQ", "USDR", "RLUSD", "UST", "BUSD", "GUSD",
}
FIAT = {"EUR", "GBP", "USD", "CAD", "JPY", "CHF", "AUD", "AED", "ZEUR", "ZGBP", "ZUSD"}
GOLD_TOKENS = {"PAXG", "XAUT"}  # tokenised gold traded as crypto on Kraken

# Metals. The bot first looks for a matching Kraken perpetual (e.g. PF_XAUUSD);
# if Kraken does not list one, it uses the COMEX futures price from Yahoo
# Finance as a reference for the move.
METALS = [
    {"name": "Gold", "code": "XAU", "yahoo": "GC=F"},
    {"name": "Silver", "code": "XAG", "yahoo": "SI=F"},
    {"name": "Copper", "code": "XCU", "yahoo": "HG=F"},
    {"name": "Platinum", "code": "XPT", "yahoo": "PL=F"},
    {"name": "Palladium", "code": "XPD", "yahoo": "PA=F"},
]
# Use Yahoo prices for metals Kraken doesn't list (Platinum, Palladium)?
# Off by default: Yahoo blocks GitHub's servers, and you can't trade them on Kraken.
YAHOO_FALLBACK = False

# ---------- Signal rules ----------
MIN_CANDLES_15M = 200
MAX_DATA_AGE_HOURS = 3          # skip markets that are closed / stale

# Backtest (30 days, majors + metals, with costs) showed only score >= 85 setups that
# follow BTC's 4H trend come out around break-even; lower scores lost money.
MIN_TECH_SCORE = 85             # technical score needed before sentiment is added
GRADE_A = 90
GRADE_B = 85
GRADE_C = 85                    # lowest score ever sent (no grade C any more)
URGENT_SCORE = 92               # sent immediately on any 5-minute scan
BTC_TREND_FILTER = True         # alts: long only if BTC 4H trend is up, short only if down

SCAN_EVERY_MIN = 5              # scan interval in loop mode
BATCH_EVERY_MIN = 15            # regular signal batch interval
MAX_PER_BATCH = 3
MIN_PER_BATCH = 0               # never force a signal: no setup = no message
MAX_PER_HOUR = 6                # hard cap so your phone isn't flooded
COOLDOWN_HOURS = 4              # don't repeat the same pair+direction
SIGNAL_EXPIRY_HOURS = 24        # signals are intraday: few hours up to 1 day
ENTRY_VALID_MINUTES = 60        # don't open the trade after this
ENTRY_ZONE_R = 0.3              # don't chase: entry only within +/-0.3R of the signal price

SL_ATR_MULT = 1.2               # stop distance in 1h ATRs (min)
SL_ATR_MAX = 2.5                # cap on stop distance in 1h ATRs
TP1_R = 1.5                     # take-profit 1 = 1.5x the risk
TP2_R = 2.5                     # take-profit 2 = 2.5x the risk

SENTIMENT_MAX_POINTS = 15       # how much news sentiment can move the score

# ---------- Position sizing (shown in each signal) ----------
ACCOUNT_SIZE = float(os.getenv("BOT_ACCOUNT_SIZE", "5000"))
RISK_PCT = float(os.getenv("BOT_RISK_PCT", "1"))   # % of account risked per trade
MAX_SPOT_BASIS_PCT = 3.0        # ignore a spot price that differs more than this (wrong units)

STATE_DIR =os.getenv("BOT_STATE_DIR", "state")
REPORTS_DIR = os.getenv("BOT_REPORTS_DIR", "reports")
