"""All tunable settings in one place. Change values here to adjust the bot."""
import os

# ---------- Telegram ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Timezone shown in messages, e.g. "Asia/Kolkata", "Europe/London", "UTC"
TIMEZONE = os.getenv("BOT_TIMEZONE", "UTC")

# ---------- Universe ----------
TOP_CRYPTO = 40                 # scan the N most-traded USD pairs on Kraken
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

GRADE_A = 75
GRADE_B = 60
GRADE_C = 48                    # lowest score ever sent
URGENT_SCORE = 80               # sent immediately on any 5-minute scan

BATCH_EVERY_MIN = 15            # regular signal batch interval
MAX_PER_BATCH = 3
MIN_PER_BATCH = 1               # if nothing is grade B+, send the best C (if any)
MAX_PER_HOUR = 6                # hard cap so your phone isn't flooded
COOLDOWN_HOURS = 4              # don't repeat the same pair+direction
SIGNAL_EXPIRY_HOURS = 24        # signals are intraday: few hours up to 1 day

SL_ATR_MULT = 1.2               # stop distance in 1h ATRs (min)
SL_ATR_MAX = 2.5                # cap on stop distance in 1h ATRs
TP1_R = 1.5                     # take-profit 1 = 1.5x the risk
TP2_R = 2.5                     # take-profit 2 = 2.5x the risk

SENTIMENT_MAX_POINTS = 15       # how much news sentiment can move the score

STATE_DIR = os.getenv("BOT_STATE_DIR", "state")
REPORTS_DIR = os.getenv("BOT_REPORTS_DIR", "reports")
