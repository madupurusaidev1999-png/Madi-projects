# Crypto & Metals Signal Bot

Sends **intraday LONG/SHORT trade signals to your Telegram** for crypto and metals, so
you can trade them manually on Kraken / Kraken Pro.

- Scans every **5 minutes** on GitHub Actions; no computer or server needed
- Sends the **best 1–3 setups every 15 minutes** (about 4–6 an hour, never more than 6)
- **Urgent alerts** for very strong setups (score ≥ 80) as soon as they appear
- Every signal has an **entry, stop-loss, TP1 and TP2**, plus the reasons for it
- Trades last **a few hours up to 1 day**
- **Result alerts** when a signal hits TP1, TP2 or the stop-loss
- A **daily report** of win rate and R gained/lost, saved in `reports/`
- **Learns**: entry triggers that win get more weight and losing ones get less

## What it scans

| Market | Source (all public, **no API key needed**) |
|---|---|
| Crypto: top 40 USD pairs by 24h volume on Kraken (changes automatically) | Kraken public API |
| Gold tokens PAXG / XAUT | Kraken Spot |
| Gold, Silver, Copper, Platinum, Palladium | Kraken Pro perpetual if Kraken lists one; otherwise the COMEX futures price as a reference |

**Sentiment** comes from CoinDesk, Cointelegraph, Decrypt, CryptoSlate, Bitcoin Magazine,
The Block, Google News (per coin and per metal), Reddit (r/CryptoCurrency, r/CryptoMarkets,
r/Bitcoin, r/Gold, r/Silverbugs), the Crypto Fear & Greed Index and CoinGecko trending coins.
Headlines are scored for bullish or bearish tone, and newer headlines count more.

## How a signal is chosen

1. **4H bias**: is the bigger trend up or down?
2. **1H trend**: EMA 20 vs EMA 50 and ADX trend strength. The bot never trades against the 1H trend.
3. **15m entry trigger**: EMA 9/21 cross, MACD flip, RSI pullback, or a breakout of the last 5 hours' range.
4. **Filters**: volume above average, RSI not stretched, price not chasing.
5. **Sentiment**: news and social mood moves the score by up to ±15 points.
6. **Levels**: the stop sits beyond the recent swing (1.2–2.5 × 1H ATR). TP1 is 1.5R and TP2 is 2.5R.

Grades: **A** ≥ 75, **B** ≥ 60, **C** ≥ 48 (lower confidence; sent only when nothing better exists).

## Setup (about 10 minutes, all from your phone or browser)

### 1. Create your Telegram bot
1. In Telegram, open **@BotFather** and send `/newbot`. Pick a name.
2. BotFather replies with a **token** like `123456789:AAH...`. Copy it.
3. Open your new bot and press **Start** (send it any message, e.g. `hi`).
4. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`.
   Find `"chat":{"id":123456789` and copy that number. It is your **chat ID**.

### 2. Add them to GitHub
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `TELEGRAM_BOT_TOKEN` = the token
- `TELEGRAM_CHAT_ID` = the chat ID

Optional: under the **Variables** tab, add `BOT_TIMEZONE` (e.g. `Asia/Kolkata`) so message
times show in your local time. The default is UTC.

### 3. Test it
Repo → **Actions → Signal Bot → Run workflow**, choose `test` and press **Run**.
You should get "✅ Signal bot is connected" on Telegram.

### 4. Turn it on
Scheduled runs start by themselves every 5 minutes. To run a scan right away, use
**Run workflow** with `scan`.

> ⚠️ **GitHub Actions minutes.** Private repos get 2,000 free minutes a month. Running
> every 5 minutes uses about 15,000–20,000, so a **private repo will run out within a few days**.
> **Public repos have unlimited free minutes**, and your Telegram token stays hidden in
> Secrets. Options: make the repo public, buy more minutes, or change the cron in
> `.github/workflows/signal-bot.yml` to `*/30 * * * *`.

## Changing settings

Everything is in [`bot/config.py`](bot/config.py): number of coins, grades, stop/target sizes,
signals per hour, cooldowns and so on.

## Run on your own computer

```bash
cd signal-bot
pip install -r requirements.txt
python -m bot.main          # prints signals to the screen if Telegram isn't configured
python -m pytest -q         # offline tests
```

## Important

This is a tool, not a guarantee. No bot wins every trade. Always use the stop-loss, risk only
a small % of your account per trade (1% is common), and check the daily report to see how
the signals actually perform before sizing up. Not financial advice.
